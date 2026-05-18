import os
import glob
import logging
import asyncio
from instagrapi import Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# إعداد السجلات (Logs) لمراقبة الأخطاء في السيرفر
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

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
    # تحديد وقت مستقطع أقصى للاتصال لمنع صفنة الفحص الأولية
    cl.request_timeout = 30.0
    
    msg = await update.message.reply_text(f"⏳ جاري فحص الحساب {username}...")
    
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: cl.login(username, password))
        
        cl.dump_settings(f"{SESSIONS_DIR}/{username}.json")
        await msg.edit_text(f"✅ تم ربط {username} بنجاح! تم حفظ الجلسة.")
    except Exception as e:
        error_msg = str(e).lower()
        if "two_factor_required" in error_msg or "challenge" in error_msg or "checkpoint" in error_msg:
            context.user_data['temp_auth'] = {"user": username, "pass": password, "client": cl}
            await msg.edit_text(f"🔐 حساب {username} يطلب كود التحقق (تأكيد الأمان/2FA).\nℹ️ أرسل الكود هنا الآن في رسالة عادية:")
        else:
            await msg.edit_text(f"❌ فشل الدخول: {str(e)}")

# --- معالجة كود التحقق والفيديو بحماية التوقيت المقطوع ---

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. معالجة كود التحقق (حل مشكلة الصفنة هنا)
    if 'temp_auth' in context.user_data:
        auth = context.user_data['temp_auth']
        code = update.message.text.strip()
        status_msg = await update.message.reply_text("⏳ جاري إرسال الكود لإنستغرام وفك القفل... يرجى الانتظار")
        
        try:
            # تعيين مهلة استجابة سريعة لإنستغرام داخل المكتبة
            auth['client'].request_timeout = 20.0
            
            # محاكاة تأخير بسيط (ثانيتين) لتبدو المحاولة كإدخال بشري طبيعي لتفادي تعليق الحساب
            await asyncio.sleep(2)
            
            loop = asyncio.get_event_loop()
            
            # تشغيل الدخول مع تحديد حد أقصى للانتظار (Timeout) لمنع الصفنة الأبدية
            await asyncio.wait_for(
                loop.run_in_executor(None, lambda: auth['client'].login(auth['user'], auth['pass'], verification_code=code)),
                timeout=25.0
            )
            
            # إذا نجح الدخول دون صفنة يتم الحفظ فوراً
            auth['client'].dump_settings(f"{SESSIONS_DIR}/{auth['user']}.json")
            await status_msg.edit_text(f"✅ تم التحقق وربط الحساب {auth['user']} بنجاح!")
            del context.user_data['temp_auth']
            
        except asyncio.TimeoutError:
            await status_msg.edit_text("❌ انتهت مهلة الاستجابة! إنستغرام استغرق وقتاً طويلاً للرد (صفنة حماية). جرب طلب كود جديد أو سجل دخول من الهاتف أولاً لتأكيد الآيبي الخاص بالسيرفر.")
            if 'temp_auth' in context.user_data: del context.user_data['temp_auth']
        except Exception as e:
            await status_msg.edit_text(f"❌ فشل التحقق، قد يكون الكود خاطئ أو انتهت صلاحيته.\nالخطأ: {e}")
            if 'temp_auth' in context.user_data: del context.user_data['temp_auth']
        return

    # 2. إذا كان المستخدم يرسل فيديو للنشر كـ Reels
    if update.message.video:
        video = update.message.video
        video_path = f"vid_{video.file_id}.mp4"
        
        status = await update.message.reply_text("📥 جاري تحميل الفيديو من تليجرام...")
        
        try:
            file = await context.bot.get_file(video.file_id)
            await file.download_to_drive(custom_path=video_path)
        except Exception as e:
            await status.edit_text(f"❌ فشل تحميل الفيديو: {str(e)}")
            return

        accounts = glob.glob(f"{SESSIONS_DIR}/*.json")
        if not accounts:
            await status.edit_text("⚠️ لا توجد حسابات مضافة ومسجلة حتى الآن. استخدم /add_account أولاً.")
            if os.path.exists(video_path): os.remove(video_path)
            return

        buttons = []
        for acc in accounts:
            name = os.path.basename(acc).replace(".json", "")
            buttons.append([InlineKeyboardButton(f"🎬 نشر على: {name}", callback_data=f"up|{name}|{video_path}")])
        
        await status.edit_text("🎯 اختر الحساب الذي ترغب بالنشر عليه:", reply_markup=InlineKeyboardMarkup(buttons))

# --- معالجة أزرار النشر التلقائي المحدثة ---

async def post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, acc_name, video_path = query.data.split("|")
    
    await query.edit_message_text(f"🚀 جاري رفع الريلز على حساب {acc_name}، يرجى الانتظار...")
    
    try:
        cl = Client()
        cl.request_timeout = 60.0 # مهلة دقيقة كاملة لرفع الفيديو
        cl.load_settings(f"{SESSIONS_DIR}/{acc_name}.json")
        
        loop = asyncio.get_event_loop()
        caption_text = "Posted via my automated bot 🤖"
        
        await asyncio.wait_for(
            loop.run_in_executor(None, lambda: cl.video_upload_to_reel(video_path, caption=caption_text)),
            timeout=65.0
        )
        await query.edit_message_text(f"✅ تم نشر الريلز بنجاح على حساب {acc_name}!")
    except asyncio.TimeoutError:
        await query.edit_message_text("❌ انتهت مهلة رفع الفيديو! السيرفر بطيء جداً أو إنستغرام قام بتقييد الرفع حالياً.")
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ أثناء عملية النشر: {str(e)}")
    finally:
        if os.path.exists(video_path):
            os.remove(video_path)
    await query.answer()

# --- تشغيل البوت ---

if __name__ == "__main__":
    TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_account", add_account))
    app.add_handler(MessageHandler(filters.VIDEO | (filters.TEXT & ~filters.COMMAND), handle_all_messages))
    app.add_handler(CallbackQueryHandler(post_callback))
    
    print("Bot is starting successfully...")
    app.run_polling()
