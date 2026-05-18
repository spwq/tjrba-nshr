import os
import glob
import logging
from instagrapi import Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# إعداد السجلات (Logs) لمراقبة الأخطاء في السيرفر
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# مجلد حفظ الجلسات (تأكد من ربط Volume في Railway بهذا المسار)
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
    msg = await update.message.reply_text(f"⏳ جاري الدخول إلى {username}...")
    
    try:
        # محاولة تسجيل الدخول العادي
        cl.login(username, password)
        # حفظ الجلسة في ملف JSON في حال نجاح الدخول مباشرة
        cl.dump_settings(f"{SESSIONS_DIR}/{username}.json")
        await msg.edit_text(f"✅ تم ربط {username} بنجاح! تم حفظ الجلسة.")
    except Exception as e:
        error_msg = str(e).lower()
        # التقاط طلب التحقق سواء كان 2FA أو Challenge (تغيير الآيبي)
        if "two_factor_required" in error_msg or "challenge" in error_msg or "checkpoint" in error_msg:
            context.user_data['temp_auth'] = {"user": username, "pass": password, "client": cl, "msg_id": msg.message_id}
            await msg.edit_text(f"🔐 حساب {username} يطلب كود التحقق (تأكيد الأمان/2FA).\nℹ️ تفقد هاتفك أو بريدك وأرسل الكود هنا الآن في رسالة:")
        else:
            await msg.edit_text(f"❌ فشل الدخول: {str(e)}")

# --- معالجة كود التحقق والفيديو ---

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. إذا كان البوت ينتظر كود التحقق من المستخدم
    if 'temp_auth' in context.user_data:
        auth = context.user_data['temp_auth']
        code = update.message.text.strip()
        status_msg = await update.message.reply_text("⏳ جاري إرسال الكود وتأكيد الحساب...")
        
        try:
            # محاولة الدخول وتمرير الكود المستلم
            auth['client'].login(auth['user'], auth['pass'], verification_code=code)
            auth['client'].dump_settings(f"{SESSIONS_DIR}/{auth['user']}.json")
            await status_msg.edit_text(f"✅ تم التحقق وربط الحساب {auth['user']} بنجاح!")
            del context.user_data['temp_auth']
        except Exception as e:
            await status_msg.edit_text(f"❌ فشل التحقق، قد يكون الكود خاطئ أو انتهت صلاحيته.\nالخطأ: {e}")
        return

    # 2. إذا كان المستخدم يرسل فيديو للنشر كـ Reels
    if update.message.video:
        video = update.message.video
        video_path = f"vid_{video.file_id}.mp4"
        
        status = await update.message.reply_text("📥 جاري تحميل الفيديو من تليجرام...")
        file = await video.get_file()
        await file.download_to_drive(video_path)
        # جلب الحسابات المسجلة من المجلد
        accounts = glob.glob(f"{SESSIONS_DIR}/*.json")
        if not accounts:
            await status.edit_text("⚠️ لا توجد حسابات مضافة ومسجلة حتى الآن. استخدم /add_account أولاً.")
            if os.path.exists(video_path): 
                os.remove(video_path)
            return

        # إنشاء أزرار شفافة بأسماء الحسابات المتاحة
        buttons = []
        for acc in accounts:
            name = os.path.basename(acc).replace(".json", "")
            buttons.append([InlineKeyboardButton(f"🎬 نشر على: {name}", callback_data=f"up|{name}|{video_path}")])
        
        await status.edit_text("Target 🎯 اختر الحساب الذي ترغب بالنشر عليه:", reply_markup=InlineKeyboardMarkup(buttons))

# --- معالجة أزرار النشر التلقائي ---

async def post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, acc_name, video_path = query.data.split("|")
    
    await query.edit_message_text(f"🚀 جاري رفع الريلز على حساب {acc_name}، يرجى الانتظار...")
    
    try:
        cl = Client()
        # تحميل الجلسة المحفوظة لتفادي تسجيل الدخول بكلمة المرور مجدداً وحماية الحساب
        cl.load_settings(f"{SESSIONS_DIR}/{acc_name}.json")
        cl.login(acc_name, "") 
        
        # رفع الفيديو كـ Reel (يمكنك تغيير الـ caption للنص الذي تريده)
        cl.video_upload_to_reel(video_path, caption="Posted via my automated bot 🤖")
        await query.edit_message_text(f"✅ تم نشر الريلز بنجاح على حساب {acc_name}!")
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ أثناء عملية النشر: {str(e)}")
    finally:
        # تنظيف السيرفر ومسح ملف الفيديو لتوفير مساحة القرص
        if os.path.exists(video_path):
            os.remove(video_path)
    await query.answer()

# --- تشغيل البوت ---

if __name__ == "__main__":
    # يفضل وضع توكين البوت في متغيرات بيئة السيرفر باسم BOT_TOKEN للأمان
    TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_account", add_account))
    app.add_handler(MessageHandler(filters.VIDEO | (filters.TEXT & ~filters.COMMAND), handle_all_messages))
    app.add_handler(CallbackQueryHandler(post_callback))
    
    print("Bot is starting...")
    app.run_polling()
