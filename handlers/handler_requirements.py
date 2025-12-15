from aiogram.types import Message, CallbackQuery
from . import del_message
from services.user_services import UserService
from logger import logger
from utils.texts import t

async def admin_require(db, message: Message) -> bool:
    """Check if the user executing the command is an admin"""
    try:
        # Distinguish between CallbackQuery and Message
        if isinstance(message, CallbackQuery):
            user = message.from_user          # user who triggered the callback
            chat = message.message.chat       # chat where the callback happened
            bot = message.message.bot         # bot instance
        else:
            user = message.from_user          # user who sent the message
            chat = message.chat               # chat where the message was sent
            bot = message.bot                 # bot instance

        # Case 1: if in a group or supergroup, check Telegram chat admin status
        if chat.type in ("group", "supergroup"):
            chat_member = await bot.get_chat_member(
                chat_id=chat.id,
                user_id=user.id
            )    
            is_admin = chat_member.status in ['administrator', 'creator']

            if not is_admin:
                # Send "not allowed" message depending on message type
                response = await (message.message.answer if isinstance(message, CallbackQuery) else message.answer)(
                    t("no_permission_cmd")
                )
                await del_message(3, response, message)
                # Optionally: delete this warning after X seconds with del_message()
                return False
            return True

        # Case 2: if not in a group (private chat), check admin status in database
        is_admin = UserService.is_admin(db=db, user_tID=str(user.id))
        if not is_admin:
            # Send "not allowed" message depending on message type
            response = await (message.message.answer if isinstance(message, CallbackQuery) else message.answer)(
                t("no_permission_cmd")
            )
            await del_message(3, response, message)
            
            return False
    
        return True
    
    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            # Log failure if even sending the error message fails
            logger.exception("Failed to send error message")  
        return False 
