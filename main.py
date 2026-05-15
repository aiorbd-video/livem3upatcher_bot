import logging
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from config import BOT_TOKEN, CHECK_TIME
import database as db
import handlers

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

async def on_startup(app: Application):
    await db.create_indexes()
    logger.info("Enterprise Extra Pro Bot RUNNING (All In One Reborn)")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", handlers.start_command))
    app.add_handler(CallbackQueryHandler(handlers.button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.admin_message_handler))

    app.job_queue.run_repeating(handlers.auto_checker_job, interval=CHECK_TIME, first=10)
    app.run_polling()

if __name__ == "__main__":
    main()
