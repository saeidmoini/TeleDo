from __future__ import annotations
from aiogram.enums import ChatType
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram import F
from logger import logger
import asyncio
from aiogram.types import Message, CallbackQuery
from utils.decorators import exception_decorator



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
                KeyboardButton(text="مدیریت تسک ها"),
                KeyboardButton(text="مدیریت کاربران"),
            ])
        
        else:
            keyboards.append(
                [
                    KeyboardButton(text="تسک های من"),

                ])

    else:
        if is_admin:
            keyboards.append([KeyboardButton(text="مدیریت تسک ها")])
            keyboards.append([KeyboardButton(text="افزودن تسک"), KeyboardButton(text="مدیریت کاربران")])
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
