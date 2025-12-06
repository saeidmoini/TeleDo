import asyncio
from logger import logger
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from config import config
from handlers import main_router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from models import User, init_db
from services.user_services import UserService
from database import get_db
from utils.texts import t

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
    """
    Register command menus for users and admins.
    - Default scope: regular users only see /tasks for their own items.
    - Admin scope (per admin chat): /tasks for full task control and /users for user management.
    """
    user_commands = [
        BotCommand(
            command="/tasks",
            description=t("cmd_user_tasks_desc"),
        ),
    ]

    admin_commands = [
        BotCommand(
            command="/tasks",
            description=t("cmd_admin_tasks_desc"),
        ),
        BotCommand(
            command="/users",
            description=t("cmd_admin_users_desc"),
        ),
    ]

    try:
        await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    except Exception:
        logger.exception("Failed to set default (user) commands")

    admin_chat_ids = set()
    db = None
    try:
        db = next(get_db())
        admins = (
            db.query(User)
            .filter(User.is_admin.is_(True), User.telegram_id.isnot(None))
            .all()
        )
        for admin in admins:
            try:
                admin_chat_ids.add(int(admin.telegram_id))
            except (TypeError, ValueError):
                logger.warning("Skipping admin with invalid telegram_id: %s", admin.telegram_id)
    except Exception:
        logger.exception("Failed to load admin list for command setup")
    finally:
        if db:
            db.close()

    for admin_chat_id in admin_chat_ids:
        try:
            await bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=admin_chat_id)
            )
        except Exception:
            logger.exception("Failed to set admin commands for chat %s", admin_chat_id)

dp = Dispatcher()

# Add router
dp.include_router(main_router)

async def on_startup(bot: Bot):
    if config.MODE.upper() == "PROD" and config.WEBHOOK_URL:
        try:
            await bot.set_webhook(config.WEBHOOK_URL)
        except Exception:
            logger.exception("Failed to set webhook on startup")
    logger.info("Bot started!")

async def on_shutdown(bot: Bot):
    if config.MODE.upper() == "PROD" and config.WEBHOOK_URL:
        try:
            await bot.delete_webhook()
        except Exception:
            logger.exception("Failed to delete webhook on shutdown")
    logger.info("Bot stopped!")

def main():
    ensure_initial_admin()
    dp.startup.register(set_commands)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    if config.MODE.upper() == "DEV":
        asyncio.run(dp.start_polling(bot))
    else:
        app = web.Application()
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
        )
        
        webhook_requests_handler.register(app, path="/webhook")
        setup_application(app, dp, bot=bot)

        async def healthcheck(request: web.Request):
            # Simple endpoint so opening the host in a browser does not return 404
            return web.Response(text="Telegram bot webhook is running")
        app.router.add_get("/", healthcheck)
        app.router.add_get("/health", healthcheck)
        
        web.run_app(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT)

if __name__ == "__main__":
    main()
