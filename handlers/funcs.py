from __future__ import annotations
from aiogram.enums import ChatType
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram import F
from logger import logger
import asyncio
from functools import wraps
from aiogram.types import Message, CallbackQuery

def exception_decorator(func):
    """
    Decorator for handling exceptions in both synchronous and asynchronous functions.
    - If the wrapped function raises an exception, it will be logged instead of crashing the bot.
    - Returns None if an exception occurs, so the bot can continue running smoothly.

    This is especially useful for service/database functions to avoid repeating try/except everywhere.
    """
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        """
        Wrapper for async functions.
        Tries to execute the function and logs any exception if it occurs.
        """
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            return None

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        """
        Wrapper for sync functions.
        Tries to execute the function and logs any exception if it occurs.
        """
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            return None

    # Decide which wrapper to return based on whether the function is async or sync
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper



# ===== Create Callback Function ======
@exception_decorator
def get_callback(callback_query, new_callback_data):
    class SimpleCallback:
        def __init__(self, original_callback, data):
            self.message = original_callback.message
            self.data = data
            self.from_user = original_callback.from_user
            self.id = original_callback.id
        
        async def answer(self, *args, **kwargs):
            pass  # Do nothing for answer in mock
    
    callback = SimpleCallback(callback_query, new_callback_data)
    return callback



@exception_decorator
def chat_type_filter(chat_type):
    """
    Create a filter for message handlers based on chat type.
    This helps to differentiate whether the command comes from a private chat,
    a group, or a supergroup.
    """
    return F.chat.type == chat_type


@exception_decorator
def get_main_menu_keyboard(chat_type: ChatType, is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Generate the main menu keyboard depending on the chat type.
    """

    keyboards = []
    
    
    if chat_type == ChatType.PRIVATE:
        if is_admin:
            keyboards.append(
            [
                KeyboardButton(text="تسک های من"), 

            ])
            keyboards.append(
            [
                KeyboardButton(text="/tasks"), 
                
            ])
            keyboards.append(
            [
                KeyboardButton(text="/add"),
                KeyboardButton(text="مدیریت کاربران"),
                
            ])
        
        else:
            keyboards.append(
                [
                    KeyboardButton(text="تسک های من"),

                ])

    else:
        if is_admin:
            keyboards.append([KeyboardButton(text="/tasks")])
            keyboards.append([KeyboardButton(text="/add"), KeyboardButton(text="مدیریت کاربران")])
        else:
            keyboards.append([KeyboardButton(text="تسک های من")])

    keyboard = ReplyKeyboardMarkup(
        keyboard=keyboards,
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False
    )

    return keyboard


@exception_decorator
async def del_message(sleep: float = 3.0, *args: Message) -> True | None :
    await asyncio.sleep(sleep)
    errors = 0
    for i in args:
        try:
            await i.delete()
        except:
            errors += 1
            continue
    if errors != 0:
        logger.exception(f"Failed to delete {errors} {"message" if errors == 1 else "messages"} from chat")

    return True
