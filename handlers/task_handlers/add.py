from .. import main_router as router
from .. import chat_type_filter, get_main_menu_keyboard, del_message
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ChatType
from aiogram import F
from database import get_db
from logger import logger
from services.task_services import TaskService
from services.user_services import UserService
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from config import config

# ===== Handler for create new task in group/supergroup chats =====
@router.message(Command("add"), chat_type_filter(ChatType.GROUP))
@router.message(Command("add"), chat_type_filter(ChatType.SUPERGROUP))
async def add_task(message: Message):
    db = None
    try:
        db = next(get_db())  # Open database session

        group = TaskService.get_group(db, tID=str(message.chat.id))

        # Check if user is an admin of the group
        chat_member = await message.bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=message.from_user.id
        )    
        is_admin = chat_member.status in ['administrator', 'creator']
        if not is_admin:
            response = await message.answer(
                "اجرای این دستور فقط توسط ادمین ممکن است ❌\n"
            )
            # Delete response and message after 3 seconds
            await del_message(3, response, message)
            return
        
        # Check if user exists in DB and is admin
        user = UserService.get_user(db=db, user_tID=str(message.from_user.id))
        if not user or not user.is_admin:
            response = await message.answer(
                "اجرای این دستور فقط توسط ادمین ممکن است ❌\n"
            )
            # Delete response and message after 3 seconds
            await del_message(3, response, message)
            return
        
        topic = None
        if message.is_topic_message:
            topic = TaskService.get_topic(db=db, tID=str(message.message_thread_id))
            if topic:
                topic = topic.id

        # User replied to another message
        if message.reply_to_message and message.reply_to_message.text and not (message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot):
            original_text = message.reply_to_message.text
            if original_text and type(original_text) == str:
                original_text = original_text.strip()
                add_res = TaskService.create_task(db=db, title=original_text, admin_id=user.id, group_id=group.id, topic_id=topic)
                if not add_res:
                    response = await message.answer("❌ مشکلی در ساخت تسک به وجود آمد. لطفاً دوباره تلاش کنید")    
                else:
                    response = await message.answer("✅ تسک با موفقیت ساخته شد.")
            else:
                response = await message.answer(
                    "❌\n"
                    "پیامی که به آن ریپلای زدید مقدار معتبری ندارد\n"
                    "و نمیتواند به عنوان نام تسک باشد"
                )
        # /add with task name directly in the same message
        elif len(message.text.strip()) > len("/add"):
                try:
                    task_name = message.text.split("/add", maxsplit=1)[1].strip()
                except Exception:
                    logger.exception("Failed to processing task_name")
                    response = await message.answer("❌ مشکلی در پردازش نام تسک به وجود آمد. لطفاً دوباره تلاش کنید")    
                add_res = TaskService.create_task(db=db, title=task_name, admin_id=user.id, group_id=group.id, topic_id=topic)
                if not add_res:
                    response = await message.answer("❌ مشکلی در ساخت تسک به وجود آمد. لطفاً دوباره تلاش کنید")    
                else:
                    response = await message.answer("✅ تسک با موفقیت ساخته شد.")
        # Invalid usage of /add command
        else:
            response = await message.answer(
                "❌ دستور شما معتبر نیست.\n"
                "برای اضافه کردن تسک دستور را به شکل `/add عنوان_تسک` ارسال کنید.\n"
                "یا ابتدا یک پیام متنی بفرستید و سپس روی همان پیام ریپلای کرده و /add را ارسال کنید."
            )
            # Delete response and message after 3 seconds
            await del_message(3, response, message)
            return

        # Delete final response and message after 3 seconds
        await del_message(3, response, message)
    
    except Exception:
        # Log unexpected error and try to notify user
        logger.exception("Unexpected error occurred")
        try:
            await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Always close database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

# ===== Handler for create new task in private chats =====
class AddTaskStates(StatesGroup):
    waiting_for_title = State()

@router.message(Command("add"), chat_type_filter(ChatType.PRIVATE))
async def add_task_in_private(message: Message, state: FSMContext):
    try:
        db = next(get_db())  # Open database session

        # Check if user exists in DB and is admin
        user = UserService.get_user(db=db, user_tID=str(message.from_user.id))
        if not user or not user.is_admin:
            response = await message.answer(
                "اجرای این دستور فقط توسط ادمین ممکن است ❌\n"
            )

            # Delete response and message after 3 seconds
            await del_message(3, response, message)
            return

        # User replied to another message
        if message.reply_to_message and message.reply_to_message.text and not (message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot):
            original_text = message.reply_to_message.text
            if original_text and type(original_text) == str:
                original_text = original_text.strip()
                add_res = TaskService.create_task(db=db, title=original_text, admin_id=user.id)
                if not add_res:
                    response = await message.answer("❌ مشکلی در ساخت تسک به وجود آمد. لطفاً دوباره تلاش کنید")    
                else:
                    response = await message.answer("✅ تسک با موفقیت ساخته شد.")
                # Delete response and message after 3 seconds
                await del_message(3, response, message)
                return
            else:
                response = await message.answer(
                    "❌\n"
                    "پیامی که به آن ریپلای زدید مقدار معتبری ندارد\n"
                    "و نمیتواند به عنوان نام تسک باشد"
                )
                # Delete response and message after 3 seconds
                await del_message(3, response, message)
                return
        # Use conversation with user to get task name
        else:
            # Create cancel keyboard
            cancel_keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ کنسل کردن")]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            
            try:
                # Store basic information in state
                await state.update_data(
                    user_id=user.id,
                    user_admin=user.is_admin,
                    chat_type=message.chat.type,
                    group_id=str(message.chat.id) if message.chat.type != ChatType.PRIVATE else None,
                    message_ids=[message.message_id]  # Store initial message_id
                )
            except Exception:
                await state.clear()
                error_response = await message.answer("❌ خطایی در ساخت تسک به وجود آمد")
                logger.exception("Failed to update state data")
                # Delete response and message after 3 seconds
                await del_message(3, error_response, message)
                return
                
            
            # Request task title
            response = await message.answer(
                "📝 لطفاً عنوان تسک را وارد کنید:\n\n"
                "یا برای کنسل کردن ❌ کنسل کردن را بزنید",
                reply_markup=cancel_keyboard
            )
            
            # Add response message_id to list
            try:
                current_data = await state.get_data()
                message_ids = current_data.get('message_ids', [])
                message_ids.append(response.message_id)
                await state.update_data(message_ids=message_ids)
            except Exception:
                await state.clear()
                error_response = await message.answer("❌ خطایی در ساخت تسک به وجود آمد")
                logger.exception("Failed to add response id to message_ids in state data")
                # Delete response and message after 3 seconds
                await del_message(3, error_response, message)
                return
                
   
            
            await state.set_state(AddTaskStates.waiting_for_title)
  
    except Exception:
        # Log unexpected error and try to notify user
        logger.exception("Unexpected error occurred")
        try:
            await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Always close database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

@router.message(AddTaskStates.waiting_for_title, F.text == "❌ کنسل کردن")
async def cancel_add_task(message: Message, state: FSMContext):
    """Handle cancel operation during task addition"""
    try:
        data = await state.get_data()
        
        # Store current message_id
        message_ids = data.get('message_ids', [])
        message_ids.append(message.message_id)
        
        # Prepare appropriate keyboard
        keyboard = get_main_menu_keyboard(chat_type=data['chat_type'], is_admin=data.get("user_admin", False))

        # Send cancellation message
        await message.answer(
            "❌ عملیات افزودن تسک کنسل شد.",
            reply_markup=keyboard
        )
        
        # Clean up previous messages
        try:
            # Delete all messages related to the add task operation
            for msg_id in message_ids:
                try:
                    await message.bot.delete_message(
                        chat_id=message.chat.id,
                        message_id=msg_id
                    )
                except Exception:
                    logger.exception(f"Failed to clean up messages")
                    continue
        except Exception:
            logger.exception(f"Failed to clean up messages")
        
        # Clear state
        await state.clear()
        
    except Exception:
        # Log unexpected error and try to notify user
        logger.exception("Unexpected error occurred")
        try:
            await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")  

@router.message(AddTaskStates.waiting_for_title)
async def process_task_and_create(message: Message, state: FSMContext):
    """Process task title and create the task immediately"""
    try:
        data = await state.get_data()

        if not message.text:
            error = await message.answer("❌ لطفاً یک عنوان معتبر وارد کنید:")
            message_ids = data.get('message_ids', [])
            message_ids.append(error.message_id)
            await state.update_data(message_ids=message_ids)
            return
        
        # Store current message_id
        message_ids = data.get('message_ids', [])
        message_ids.append(message.message_id)
        
        db = next(get_db())
        
        # Create task with only title and admin ID (other fields will be None)
        task = TaskService.create_task(
            db=db,
            admin_id=data['user_id'],
            title=message.text,
        )
        if not task:
            error = await message.answer("❌ خطایی در ساخت تسک پیش آمد. لطفاً دوباره تلاش کنید")
            message_ids = data.get('message_ids', [])
            message_ids.append(error.message_id)
            await state.update_data(message_ids=message_ids)
            return

        # Prepare appropriate keyboard
        keyboard = get_main_menu_keyboard(chat_type=data['chat_type'], is_admin=data.get('user_admin', False))
        
        # Send final confirmation message
        final_response = await message.answer(
            f"✅ تسک با موفقیت ایجاد شد!\n\n"
            f"📋 عنوان: {message.text}\n"
            f"📝 توضیحات: بدون توضیح\n"
            f"⏰ شروع: تعیین نشده\n"
            f"⏰ پایان: تعیین نشده",
            reply_markup=keyboard
        )
        
        try:
            # Delete all messages related to the add task operation
            for msg_id in message_ids:
                try:
                    await message.bot.delete_message(
                        chat_id=message.chat.id,
                        message_id=msg_id
                    )
                except Exception:
                    logger.exception(f"Failed to delete message")
                    continue
            
        except Exception:
            logger.exception(f"Failed to clean up messages")
        
        # Clear state
        await state.clear()
        
    except Exception:
        # Log unexpected error and try to notify user
        logger.exception("Unexpected error occurred")
        try:
            await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")
    
    finally:
        # Always close database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")
