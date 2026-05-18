import os
import glob
import logging
import asyncio
from instagrapi import Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# إعداد السجلات (Logs) لمراقبة السيرفر والأخطاء
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# مجلد حفظ الجلسات (تأكد من ربط Volume ثابت في الاستضافة لهذا المسار)
SESSIONS_DIR = "sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

# --- أوامر البوت ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً بك في بوت إدارة ريلز إنستقرام المطور\n\n"
        "🔹 لإضافة حساب: /add_account user:pass \n"
        "🔹 للنشر: فقط أرسل مقطع الفيديو (MP4)\n"
        "------------------------------------"
    )

# --- إضافة الحسابات والتعامل مع تحديات الأمان ---

async def add_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or ":" not in context.args[0]:
        await update.message.reply_text("❌ أرسل الحساب بالصيغة: /add_account user:pass ")
        return

    username, password = context.args[0].split(":", 1)
    cl = Client()
    msg = await update.message.reply_text(f"⏳ جاري فحص الحساب {username}...")
    
    try:
        # تشغيل تسجيل الدخول في الخلفية لمنع الصفنة أول مرة
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: cl.login(username, password))
        
        # حفظ الجلسة فوراً عند النجاح
        cl.dump_settings(f"{SESSIONS_DIR}/{username}.json")
        await msg.edit_text(f"✅ تم ربط {username} بنجاح! تم حفظ الجلسة.")
    except Exception as e:
        error_msg = str(e).lower()
        # التقاط طلب التحقق سواء كان 2FA أو Challenge (تغيير الآيبي)
        if "two_factor_required" in error_msg or "challenge" in error_msg or "checkpoint" in error_msg:
            context.user_data['temp_auth'] = {"user": username, "pass": password, "client": cl}
            await msg.edit_text(f"🔐 حساب {username} يطلب كود التحقق (تأكيد الأمان/2FA).\nℹ️ تفقد هاتفك أو بريدك وأرسل الكود هنا الآن في رسالة عادية:")
        else:
            await msg.edit_text(f"❌ فشل الدخول: {str(e)}")

# --- معالجة كود التحقق وتحميل الفيديو بدون تعليق ---

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. إذا كان البوت ينتظر كود التحقق من المستخدم (حل مشكلة الصفنة)
    if 'temp_auth' in context.user_data:
        auth = context.user_data['temp_auth']
        code = update.message.text.strip()
        status_msg = await update.message.reply_text("⏳ جاري إرسال الكود وتأكيد الحساب... يرجى الانتظار")
        
        try:
            # تشغيل التحقق في الخلفية لمنع البوت من التعليق
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, 
                lambda: auth['client'].login(auth['user'], auth['pass'], verification_code=code)
            )
            
            # حفظ الجلسة بعد نجاح الكود
            auth['client'].dump_settings(f"{SESSIONS_DIR}/{auth['user']}.json")
            await status_msg.edit_text(f"✅ تم التحقق وربط الحساب {auth['user']} بنجاح!")
            del context.user_data['temp_auth']
            
        except Exception as e:
            await status_msg.edit_text(f"❌ فشل التحقق، قد يكون الكود خاطئ أو انتهت صلاحيته.\nالخطأ: {e}")
            del context.user_data['temp_auth'] # تنظيف البيانات للمحاولة مجدداً
        return

    # 2. إذا كان المستخدم يرسل فيديو للنشر كـ Reels (حل مشكلة تعليق التحميل)
    if update.message.video:
        video = update.message.video
        video_path = f"vid_{video.file_id}.mp4"
        
        status = await update.message.reply_text("📥 جاري تحميل الفيديو من تليجرام...")
        
        try:
            # الطريقة الأحدث والأضمن للتحميل دون صفنة في V21+
            file = await context.bot.get_file(video.file_id)
            await file.download_to_drive(custom_path=video_path)
        except Exception as e:
            await status.edit_text(f"❌ فشل تحميل الفيديو: {str(e)}")
            return

        # جلب جميع الحسابات المسجلة بالمجلد
        accounts = glob.glob(f"{SESSIONS_DIR}/*.json")
        if not accounts:
            await status.edit_text("⚠️ لا توجد حسابات مضافة ومسجلة حتى الآن. استخدم /add_account أولاً.")
            if os.path.exists(video_path): 
                os.remove(video_path)
            return

        # إنشاء أزرار الحسابات المتاحة للاختيار
        buttons = []
        for acc in accounts:
            name = os.path.basename(acc).replace(".json", "")
            buttons.append([InlineKeyboardButton(f"🎬 نشر على: {name}", callback_data=f"up|{name}|{video_path}")])
        
        await status.edit_text("🎯 اختر الحساب الذي ترغب بالنشر عليه:", reply_markup=InlineKeyboardMarkup(buttons))

# --- معالجة أزرار النشر التلقائي ---

async def post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, acc_name, video_path = query.data.split("|")
    
    await query.edit_message_text(f"🚀 جاري رفع الريلز على حساب {acc_name}، يرجى الانتظار...")
    
    try:
        cl = Client()
        # تحميل الجلسة المحفوظة لتفادي طلب الرمز مجدداً
        cl.load_settings(f"{SESSIONS_DIR}/{acc_name}.json")
        
        # النشر في الخلفية لمنع تعليق أزرار البوت أثناء الرفع الثقيل
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: cl.login(acc_name, ""))
        await loop.run_in_executor(None, lambda: cl.video_upload_to_reel(video_path, caption="Posted via my automated bot 🤖"))
        
        await query.edit_message_text(f"✅ تم نشر الريلز بنجاح على حساب {acc_name}!")
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ أثناء عملية النشر: {str(e)}")
    finally:
        # مسح الفيديو من السيرفر فوراً لتوفير المساحة
        if os.path.exists(video_path):
            os.remove(video_path)
    await query.answer()

# --- تشغيل البوت ---

if __name__ == "__main__":
    # وضع توكين البوت في متغيرات بيئة السيرفر باسم BOT_TOKEN للأمان
    TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_account", add_account))
    app.add_handler(MessageHandler(filters.VIDEO | (filters.TEXT & ~filters.COMMAND), handle_all_messages))
    app.add_handler(CallbackQueryHandler(post_callback))
    
    print("Bot is starting successfully...")
    app.run_polling()
