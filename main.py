import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from config import BOT_TOKEN, CHECK_TIME

# 🎯 admin_message_handler ইম্পোর্ট করা হয়েছে
from handlers import start_command, button_handler, admin_message_handler, auto_checker_job

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is missing! Please set it in environment variables.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # ==========================================
    # 🟢 Handlers (বটের ব্রেইন)
    # ==========================================
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # 🎯 ফিক্স: এই লাইনটি যুক্ত করার ফলেই বাটনগুলো কাজ করবে!
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_message_handler))

    # ==========================================
    # 🔄 Auto Background Checker
    # ==========================================
    app.job_queue.run_repeating(auto_checker_job, interval=CHECK_TIME, first=10)

    logger.info("Enterprise Extra Pro Bot is RUNNING Perfectly...")
    app.run_polling()

if __name__ == "__main__":
    main()
