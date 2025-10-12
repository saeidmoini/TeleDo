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
import re

@router.message(F.data == "مدیریت کاربران")
async def view_users(db=None, message: Message = None, original_message_id: int = None, callback_query: CallbackQuery = None, user_tID = None):
    """
    Display all users with interactive action buttons.
    Shows promote/demote, delete, and info buttons for each user.
    """
    try:
        if db is None:
            db = next
        # Get users generator (excludes current user)
        user_tID = message.from_user.id if message else user_tID
        users_generator = UserService.get_all_users(db=db, user_tID=user_tID)
        
        # Initialize empty keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        user_count = 0
        
        # Process each user in the generator
        for user in users_generator:
            user_count += 1
            
            # Create appropriate button based on admin status
            toggle_finish_callback = f"toggle_user|{user.id}|{user_tID}|{original_message_id}" if original_message_id else f"toggle_user|{user.id}|{user_tID}"
            admin_button = InlineKeyboardButton(
                text="⬇️ تبدیل به کاربر عادی" if user.is_admin else "⬆️ ادمین کردن", 
                callback_data=toggle_finish_callback
            )
            
            # Add user action buttons in one row
            del_finish_callback = f"del_user|{user.id}|{user_tID}|{original_message_id}" if original_message_id else f"del_user|{user.id}|{user_tID}"
            keyboard.inline_keyboard.append([
                admin_button,
                InlineKeyboardButton(text="🗑 حذف", callback_data=del_finish_callback),
                InlineKeyboardButton(text=f"👤 {user.username}", callback_data=f"info|{user.id}")
            ])
        
        # Check if no users were found
        if user_count == 0:
            response = await message.answer("❌ هیچ کاربری پیدا نشد")
            await del_message(3, response, message)
            return
        
        # Add finish operation button at the bottom
        finish_callback = f"finish_operation|{original_message_id}" if original_message_id else "finish_operation"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="اتمام عملیات ✅", callback_data=finish_callback),
        ])

        # Add refresh operation button at the bottom
        refresh_callback = f"refresh_operation|{original_message_id}|{user_tID}"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="رفرش 🔄", callback_data=refresh_callback),
        ])
        
        # Send the message with user list
        if callback_query is None:
            await message.answer(
                f"👥 مدیریت کاربران (تعداد: {user_count})",
                reply_markup=keyboard
            )
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
                await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            else:
                return
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Always close the database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")
                
async def add_user_with_reply(db, message: Message):
    """Add a user by replying to a message containing their username"""
    original_text = message.reply_to_message.text
    if original_text and type(original_text) == str:
        # Clean and normalize username
        original_text = original_text.strip()
        original_text = original_text[1:] if original_text.startswith("@") else original_text
        
        # Invalid username if it contains spaces
        if any(char.isspace() for char in original_text):
            response = await message.answer("❌ نام کاربری معتبر نیست")
            await del_message(3, response, message)
            return
        
        # Check if user already exists
        user_exist = UserService.get_user(db=db, username=original_text)
        if user_exist is not None:
            response = await message.answer("❌ این کاربر از قبل وجود دارد")
            await del_message(3, response, message)
            return

        # Try to add user to database
        add_res = UserService.get_or_create_user(db=db, username=original_text)
        if not add_res:
            response = await message.answer("❌ مشکلی در افزودن کاربر به وجود آمد. لطفاً دوباره تلاش کنید")    
        else:
            response = await message.answer("✅ کاربر با موفقیت اضافه شد.")
    else:
        # If reply does not contain a valid username
        response = await message.answer(
            "❌\n"
            "پیامی که به آن ریپلای زدید مقدار معتبری ندارد\n"
            "و نمیتواند به عنوان نام کاربری باشد"
        )

    # Delete final response and original message after 3 seconds
    await del_message(3, response, message)

async def add_user_directly(db, message: Message, username: str):
    """Add a user directly from /user command with username"""    
    
    # Check if user already exists
    user_exist = UserService.get_user(db=db, username=username)
    if user_exist is not None:
        response = await message.answer("❌ این کاربر از قبل وجود دارد")
        await del_message(3, response, message)
        return

    # Try to add user
    add_res = UserService.get_or_create_user(db=db, username=username)
    if not add_res:
        response = await message.answer("❌ مشکلی در افزودن کاربر به وجود آمد. لطفاً دوباره تلاش کنید")    
    else:
        response = await message.answer("✅ کاربر با موفقیت اضافه شد.")

    # Delete final response and message after 3 seconds
    await del_message(3, response, message)
    return

@router.callback_query(F.data.startswith("del_user|"))
async def handle_del_user(callback_query: CallbackQuery):
    """Delete a user directly from /user command with username"""
    db = None
    try:
        db = next(get_db())
        user_ID = None 
        try:
            if callback_query is not None:
                original_message_id = None
                parts = callback_query.data.split("|")
                user_ID = parts[1]
                user_tID = parts[2]
                if len(parts) == 4:                    
                    original_message_id = parts[3]
        except Exception:
            user_ID = None
            logger.exception("Failed to extract username from callback_query")
            await callback_query.answer("❌ مشکلی در پیدا کردن این کاربر به وجود آمد")
            return

        # Delete the user
        del_user = UserService.del_user(db=db, user_ID=user_ID)

        if del_user is None:
            await callback_query.answer("❌ مشکلی در حذف کاربر به وجود آمد. لطفاً دوباره تلاش کنید")
        elif del_user == "NOT_EXIST":
            await callback_query.answer("❌ این کاربر وجود ندارد")
        else:
            await callback_query.answer("✅ کاربر با موفقیت حذف شد.")

        # Refresh the user list after deletion
        if original_message_id:
            await view_users(db=db, original_message_id=original_message_id, callback_query=callback_query, user_tID=user_tID)
        else:
            await view_users(db=db, callback_query=callback_query, user_tID=user_tID)
    
    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
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

@router.callback_query(F.data.startswith("refresh_operation|"))
async def handle_refresh(callback_query: CallbackQuery):
    """Handle refresh operation to update the user list"""
    db = None
    try:
        db = next(get_db())
        try:
            if callback_query is not None:
                original_message_id = None
                parts = callback_query.data.split("|")
                user_tID = parts[2]
                original_message_id = parts[1]                  
                    
        except Exception:
            user_ID = None
            logger.exception("Failed to extract username from callback_query")
            await callback_query.answer("❌ مشکلی در رفرش به وجود آمد")
            return

        # Refresh the user list view
        await view_users(db=db, original_message_id=original_message_id, callback_query=callback_query, user_tID=user_tID)
    
    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
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

@router.callback_query(F.data.startswith("finish_operation"))
async def finish_operation(callback_query: CallbackQuery):
    """
    Handle finish operation callback - delete the management message and original /user message
    """
    try:
        # Extract original message ID if it exists
        parts = callback_query.data.split("|")
        original_message_id = int(parts[1]) if len(parts) > 1 else None
        
        # Delete user management message
        await callback_query.message.delete()
        
        # Delete original /user message if it exists
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
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")   

@router.callback_query(F.data.startswith("toggle_user|"))
async def handle_toggle_user(callback_query: CallbackQuery):
    """
    Handle user role toggle (admin/normal user)
    Extracts user_tID from callback data and toggles their role
    """
    db = None
    try:
        # Get database session
        db = next(get_db())

        # Extract user_tID from callback data
        try:
            if callback_query is not None:
                original_message_id = None
                parts = callback_query.data.split("|")
                user_ID = parts[1]
                user_tID = parts[2]
                if len(parts) == 4:                    
                    original_message_id = parts[3]

        except Exception:
            user_ID = None
            logger.exception("Failed to extract username from callback_query")
            await callback_query.answer("❌ مشکلی در پیدا کردن این کاربر به وجود آمد")
            return

        # Toggle user role using service
        res = UserService.toggle_user(db=db, user_ID=user_ID)
        if not res:
            await callback_query.answer("❌ مشکلی در تغییر رول این کاربر به وجود آمد")
            return

        # Send success confirmation and refresh the view
        await callback_query.answer("✅ رول کاربر با موفقیت تغییر کرد")
        if original_message_id:
            await view_users(db=db, original_message_id=original_message_id, callback_query=callback_query, user_tID=user_tID)
        else:
            await view_users(db=db, callback_query=callback_query, user_tID=user_tID)
    
    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Ensure database connection is always closed
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close database connection")

# ===== Handler for create new user =====
@router.message(Command("user"))
async def add_user(message: Message):
    """Main handler for adding a user with /user command"""
    db = None
    try:
        db = next(get_db())  # Open a database session

        # Check admin permission before proceeding
        permission = await admin_require(db=db, message=message)
        if not permission:
            return

        # Case 1: User replied to a message with a username
        if message.reply_to_message and message.reply_to_message.text and message.reply_to_message.from_user.username and message.reply_to_message.from_user.username != config.BOT_USERNAME:
            await add_user_with_reply(db=db, message=message)
        
        # Case 2: Directly used /user with username in message (Add | Delete)
        elif len(message.text.strip()) > len("/user"):
            add_match = re.match(r'^/user\s*@?(\w+)', message.text.strip())
            del_match = re.match(r'^/user\s*-\s*@?(\w+)', message.text.strip())
            if add_match:
                username = add_match.group(1)
                await add_user_directly(db=db, message=message, username=username)
            elif del_match:
                username = del_match.group(1)
                await del_user_directly(db=db, message=message, username=username)
            else:
                response = await message.answer("❌ دستور وارد شده معتبر نیست")
                await del_message(3, response, message)
                return 
        
        # Case 3: show all users to manage
        else:
            await view_users(db=db, message=message, original_message_id=message.message_id, user_tID=str(message.from_user.id))
   
    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Always close the database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")