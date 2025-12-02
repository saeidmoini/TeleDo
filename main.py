from logger import logger
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from config import config
from handlers import main_router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand
from models import init_db
from services.user_services import UserService
from database import get_db

init_db()


def ensure_initial_admin():
    """Create or promote the bootstrap admin configured in environment variables."""
    admin_id = config.INITIAL_ADMIN_ID
    admin_username = config.INITIAL_ADMIN_USERNAME
    if not admin_id and not admin_username:
        return

    db = None
    try:
        db = next(get_db())
        user = UserService.get_user(
            db=db,
            user_tID=str(admin_id) if admin_id else None,
            username=admin_username,
        )
        if user:
            changed = False
            if admin_id and user.telegram_id != str(admin_id):
                user.telegram_id = str(admin_id)
                changed = True
            if not user.is_admin:
                user.is_admin = True
                changed = True
            if changed:
                db.commit()
                db.refresh(user)
        else:
            username = admin_username or f"admin_{admin_id}"
            UserService.get_or_create_user(
                db=db,
                username=username,
                telegram_id=str(admin_id) if admin_id else None,
                is_admin=True,
            )
    except Exception:
        logger.exception("Failed to bootstrap initial admin")
    finally:
        if db:
            db.close()

# Just when we need proxy
if config.PROXY_URL:
    session = AiohttpSession(
        proxy=config.PROXY_URL
    )

    # Create bot with proxy session
    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode='HTML')
    )

# Create bot and dispatcher
else:
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

# Set commands
async def set_commands(bot: Bot):
    try:
        commands = [
            BotCommand(command="/tasks", description="نمایش و مدیریت تمام تسک‌ها"),
            BotCommand(command="/add", description='افزودن تسک جدید. مثال: "نام_تسک add/"'),
            BotCommand(command="/user", description='افزودن یا اختصاص کاربر به تسک. مثال: " نام_کاربری user/ "'),
            BotCommand(command="/name", description='تغییر نام تسک. مثال: " نام_جدید name/ "'),
            BotCommand(command="/des", description='ویرایش توضیحات تسک. مثال: " توضیحات_جدید des/ "'),
            BotCommand(command="/attach", description='افزودن فایل یا تصویر به تسک. کافی است روی فایل یا تصویر ریپلای کرده و بنویسید "attach/"'),
            BotCommand(command="/time", description='تعیین یا تغییر زمان تسک. مثال: " 2025-10-08 time/ "')
        ]

        await bot.set_my_commands(commands)
    except Exception:
        logger.exception("Faild to add commands to bot")

dp = Dispatcher()

# Add router
dp.include_router(main_router)

async def on_startup(bot: Bot):
    #await bot.set_webhook(config.WEBHOOK_URL)
    logger.info("Bot started!")

async def on_shutdown(bot: Bot):
    #await bot.delete_webhook()
    logger.info("Bot stopped!")

def main():
    ensure_initial_admin()
    dp.startup.register(set_commands)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    
    web.run_app(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT)

if __name__ == "__main__":
    main()
