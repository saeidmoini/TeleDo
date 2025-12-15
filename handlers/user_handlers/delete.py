from aiogram.types import Message
from services.user_services import UserService
from .. import del_message
from aiogram import F
from logger import logger
from utils.texts import t

async def del_user_directly(db, message: Message = None, username: str = None):
    """Delete a user directly from /user command with username"""
    try:

        # Delete the user
        del_user = UserService.del_user(db=db, username=username)

        if del_user is None:
            response = await message.answer(t("user_delete_error"))
        elif del_user == "NOT_EXIST":
            response = await message.answer(t("user_not_found"))
        else:
            response = await message.answer(t("user_delete_success"))

        # Delete final response and message after 3 seconds
        await del_message(3, response, message)
        return
    
    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message")
