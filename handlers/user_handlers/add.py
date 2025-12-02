from .. import main_router as router
from .. import del_message, admin_require
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.filters import Command
from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from database import get_db
from .delete import del_user_directly
from logger import logger
from services.user_services import UserService
from config import config
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters.state import StateFilter
import re


# ===== Main view: Show and manage users =====
@router.message(F.text == "مدیریت کاربران")
async def view_users(
    message: Message = None,
    db=None,
    original_message_id: int = None,
    callback_query: CallbackQuery = None,
    user_tID: str = None
):
    """
    Display all users with interactive action buttons.
    Shows promote/demote, delete, and info buttons for each user.
    """
    try:
        if db is None:
            db = next(get_db())

        # Only admins may manage users
        permission = await admin_require(db, message or callback_query)
        if not permission:
            return

        # Determine current user's Telegram ID
        user_tID = message.from_user.id if message else user_tID

        # Get all users except the one who triggered the view
        users_generator = UserService.get_all_users(db=db, user_tID=user_tID)

        # Prepare inline keyboard for user management
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        user_count = 0
        
        if message:
            try:
                await message.delete()
            except:
                pass

        # Iterate through users
        for user in users_generator:
            user_count += 1

            # Define toggle admin button
            toggle_callback = (
                f"toggle_user|{user.id}|{user_tID}|{original_message_id}"
                if original_message_id
                else f"toggle_user|{user.id}|{user_tID}"
            )
            admin_button = InlineKeyboardButton(
                text="⬇️ تبدیل به کاربر عادی" if user.is_admin else "⬆️ ادمین کردن",
                callback_data=toggle_callback
            )

            # Define delete button
            delete_callback = (
                f"del_user|{user.id}|{user_tID}|{original_message_id}"
                if original_message_id
                else f"del_user|{user.id}|{user_tID}"
            )

            # Append buttons for this user
            keyboard.inline_keyboard.append([
                admin_button,
                InlineKeyboardButton(text="🗑 حذف", callback_data=delete_callback),
                InlineKeyboardButton(text=f"👤 {user.username}", callback_data=f"info|{user.id}")
            ])

        # Add "Add user" button
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="➕ افزودن کاربر", callback_data="add_user")])

        # Handle empty user list
        if user_count == 0:
            response = await message.answer("❌ هیچ کاربری پیدا نشد")
            await del_message(3, response)
            return

        # Add "Finish" and "Refresh" buttons
        finish_callback = f"finish_operation|{original_message_id}" if original_message_id else "finish_operation"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="✅ اتمام عملیات", callback_data=finish_callback),
        ])

        refresh_callback = f"refresh_operation|{original_message_id}|{user_tID}"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="🔄 رفرش", callback_data=refresh_callback),
        ])

        # Send or edit message accordingly
        if callback_query is None:
            await message.answer(f"👥 مدیریت کاربران (تعداد: {user_count})", reply_markup=keyboard)
        else:
            try:
                await callback_query.message.edit_text(
                    f"👥 مدیریت کاربران (تعداد: {user_count})",
                    reply_markup=keyboard
                )
                await callback_query.answer()
            except TelegramBadRequest:
                await callback_query.answer("رفرش با موفقیت انجام شد 🔄")
            except Exception:
                logger.exception("Faild to edit user-management message after refresh")
                await callback_query.answer("❌ مشکلی در رفرش به وجود آمد")

    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            if message:
                await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            elif callback_query:
                await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")
    finally:
        # Always close the database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")


# ===== Delete user handler =====
@router.callback_query(F.data.startswith("del_user|"))
async def handle_del_user(callback_query: CallbackQuery):
    """Delete a user directly from the management menu"""
    db = None
    try:
        db = next(get_db())

        # Extract parameters from callback data
        original_message_id = None
        parts = callback_query.data.split("|")
        user_ID = parts[1]
        user_tID = parts[2]
        if len(parts) == 4:
            original_message_id = parts[3]

        # Perform delete action
        del_user = UserService.del_user(db=db, user_ID=user_ID)
        if del_user is None:
            await callback_query.answer("❌ مشکلی در حذف کاربر به وجود آمد. لطفاً دوباره تلاش کنید")
        elif del_user == "NOT_EXIST":
            await callback_query.answer("❌ این کاربر وجود ندارد")
        else:
            await callback_query.answer("✅ کاربر با موفقیت حذف شد.")

        # Refresh view after delete
        await view_users(
            db=db,
            callback_query=callback_query,
            original_message_id=original_message_id,
            user_tID=user_tID
        )

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")


# ===== Refresh operation handler =====
@router.callback_query(F.data.startswith("refresh_operation|"))
async def handle_refresh(callback_query: CallbackQuery):
    """Handle refresh operation to update the user list"""
    db = None
    try:
        db = next(get_db())

        # Extract params from callback data
        parts = callback_query.data.split("|")
        original_message_id = parts[1]
        user_tID = parts[2]

        # Refresh view
        await view_users(
            db=db,
            callback_query=callback_query,
            original_message_id=original_message_id,
            user_tID=user_tID
        )

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")


# ===== Finish operation handler =====
@router.callback_query(F.data.startswith("finish_operation"))
async def finish_operation(callback_query: CallbackQuery):
    """Handle finish operation callback - delete management message"""
    try:
        parts = callback_query.data.split("|")
        original_message_id = int(parts[1]) if len(parts) > 1 else None

        # Delete management message
        await callback_query.message.delete()

        # Try deleting original message if exists
        if original_message_id:
            try:
                await callback_query.message.bot.delete_message(
                    chat_id=callback_query.message.chat.id,
                    message_id=original_message_id
                )
            except Exception as e:
                logger.warning(f"Could not delete original message: {e}")

        await callback_query.answer("✅ عملیات با موفقیت پایان یافت")

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")


# ===== Toggle user role handler =====
@router.callback_query(F.data.startswith("toggle_user|"))
async def handle_toggle_user(callback_query: CallbackQuery):
    """Handle toggling user role (admin <-> normal)"""
    db = None
    try:
        db = next(get_db())

        # Extract parameters
        parts = callback_query.data.split("|")
        user_ID = parts[1]
        user_tID = parts[2]
        original_message_id = parts[3] if len(parts) == 4 else None

        # Toggle user role
        res = UserService.toggle_user(db=db, user_ID=user_ID)
        if not res:
            await callback_query.answer("❌ مشکلی در تغییر رول این کاربر به وجود آمد")
            return

        await callback_query.answer("✅ رول کاربر با موفقیت تغییر کرد")

        # Refresh updated view
        await view_users(
            db=db,
            callback_query=callback_query,
            original_message_id=original_message_id,
            user_tID=user_tID
        )

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close database connection")


# ===== FSM state for adding new user =====
class AddUserStates(StatesGroup):
    waiting_for_username = State()


# ===== Start add-user process =====
@router.callback_query(F.data == "add_user")
async def start_add_user(callback_query: CallbackQuery, state: FSMContext):
    """Start FSM for adding a new user"""
    try:
        await callback_query.answer()
        await state.update_data(
            orig_manage_message_id=callback_query.message.message_id,
            user_tID=str(callback_query.from_user.id)
        )
        msg = await callback_query.message.answer(
            "لطفاً یوزرنیم کاربر را ارسال کنید یا به پیام کاربر ریپلای کنید.\nمثال: @username"
        )
        await state.update_data(prompt_message_id=msg.message_id)
        await state.set_state(AddUserStates.waiting_for_username)
    except Exception:
        logger.exception("Failed to start add-user flow")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to answer callback_query in start_add_user")


# ===== Add user helper =====
async def add_user_directly(db, message: Message, username: str):
    """Add a user directly by username"""
    user_exist = UserService.get_user(db=db, username=username)
    if user_exist is not None:
        response = await message.answer("❌ این کاربر از قبل وجود دارد")
        await del_message(3, response, message)
        return

    add_res = UserService.get_or_create_user(db=db, username=username)
    if not add_res:
        response = await message.answer("❌ مشکلی در افزودن کاربر به وجود آمد. لطفاً دوباره تلاش کنید")
    else:
        response = await message.answer("✅ کاربر با موفقیت اضافه شد.")

    await del_message(3, response, message)
    return


# ===== Handle admin response during add-user FSM =====
@router.message(StateFilter(AddUserStates.waiting_for_username))
async def process_add_user_input(message: Message, state: FSMContext):
    """Handle username input during FSM"""
    db = None
    try:
        db = next(get_db())
        data = await state.get_data()
        orig_manage_message_id = data.get("orig_manage_message_id")
        user_tID = data.get("user_tID") or str(message.from_user.id)

        text = (message.text or "").strip()
        add_match = re.match(r'^@?(\w+)', text)
        if add_match:
            username = add_match.group(1)
            await add_user_directly(db=db, message=message, username=username)
        else:
            response = await message.answer(
                "❌ دستور وارد شده معتبر نیست. یوزرنیم را به صورت @username ارسال کنید یا به پیام کاربر ریپلای کنید."
            )
            await del_message(3, response, message)
            return
        await message.bot.delete_message(chat_id=message.chat.id, message_id=orig_manage_message_id)
        await view_users(db=db, message=message, original_message_id=None, user_tID=user_tID)

    except Exception:
        logger.exception("Unexpected error in add-user flow")
        try:
            await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message in process_add_user_input")
    
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db after add-user")

        if (prompt_message_id := data.get("prompt_message_id", None)):
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_message_id)
                await state.clear()
            except Exception:
                logger.exception("Failed to delete prompt message after add-user")
