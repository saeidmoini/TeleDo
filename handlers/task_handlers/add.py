from .. import main_router as router
from .. import chat_type_filter, get_main_menu_keyboard, del_message
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
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
from utils.texts import t

CANCEL_TEXT = t("btn_cancel")

# ===== Handler for create new task in group/supergroup chats =====
@router.message(Command("add"), chat_type_filter(ChatType.GROUP))
@router.message(Command("add"), chat_type_filter(ChatType.SUPERGROUP))
@router.message(F.text == "افزودن تسک", chat_type_filter(ChatType.GROUP))
@router.message(F.text == "افزودن تسک", chat_type_filter(ChatType.SUPERGROUP))
async def add_task(message: Message):
    db = None
    try:
        db = next(get_db())  # Open database session

        group = TaskService.get_group(db, tID=str(message.chat.id))
        if not group:
            group = TaskService.get_or_create_group(db=db, telegram_group_id=str(message.chat.id), name=message.chat.title)
            if not group:
                response = await message.answer(t("task_group_create_error"))
                await del_message(3, response, message)
                return

        # Check if user is an admin of the group
        chat_member = await message.bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=message.from_user.id
        )    
        is_admin = chat_member.status in ['administrator', 'creator']
        if not is_admin:
            response = await message.answer(
                t("no_permission_cmd")
            )
            # Delete response and message after 3 seconds
            await del_message(3, response, message)
            return
        
        # Check if user exists in DB and is admin
        user = UserService.get_user(
            db=db,
            user_tID=str(message.from_user.id),
            username=message.from_user.username,
        )
        if not user or not user.is_admin:
            response = await message.answer(
                t("no_permission_cmd")
            )
            # Delete response and message after 3 seconds
            await del_message(3, response, message)
            return
        
        topic = None
        if message.is_topic_message:
            topic = TaskService.get_topic(db=db, tID=str(message.message_thread_id))
            if topic:
                topic = topic.id

        # Require reply to a message for task creation
        if not (message.reply_to_message and message.reply_to_message.text and not (message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot)):
            response = await message.answer(t("commands_reply_required"))
            await del_message(3, response, message)
            return

        original_text = message.reply_to_message.text.strip()
        if not original_text:
            response = await message.answer(t("task_invalid_reply_text"))
            await del_message(3, response, message)
            return

        add_res = TaskService.create_task(db=db, title=original_text, admin_id=user.id, group_id=group.id, topic_id=topic)
        if not add_res:
            response = await message.answer(t("task_create_failed"))
        else:
            response = await message.answer(t("task_create_success"))

        # Delete final response and message after 3 seconds
        await del_message(3, response, message)
    
    except Exception:
        # Log unexpected error and try to notify user
        logger.exception("Unexpected error occurred")
        try:
            await message.answer(t("generic_error"))
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
    confirming_task = State()

@router.message(Command("add"), chat_type_filter(ChatType.PRIVATE))
@router.message(F.text == "افزودن تسک", chat_type_filter(ChatType.PRIVATE))
async def add_task_in_private(message: Message, state: FSMContext):
    try:
        db = next(get_db())  # Open database session

        # Check if user exists in DB and is admin
        user = UserService.get_user(
            db=db,
            user_tID=str(message.from_user.id),
            username=message.from_user.username,
        )
        if not user or not user.is_admin:
            response = await message.answer(t("no_permission_cmd"))

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
                    response = await message.answer(t("task_create_failed"))
                else:
                    response = await message.answer(t("task_create_success"))
                # Delete response and message after 3 seconds
                await del_message(3, response, message)
                return
            else:
                response = await message.answer(
                    t("task_invalid_reply_text")
                )
                # Delete response and message after 3 seconds
                await del_message(3, response, message)
                return
        # Use conversation with user to get task name
        else:
            # Create cancel keyboard
            cancel_keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=CANCEL_TEXT)]],
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
                error_response = await message.answer(t("generic_error"))
                logger.exception("Failed to update state data")
                # Delete response and message after 3 seconds
                await del_message(3, error_response, message)
                return
                
            
            # Request task title
            response = await message.answer(
                t("task_enter_title_prompt"),
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
                error_response = await message.answer(t("generic_error"))
                logger.exception("Failed to add response id to message_ids in state data")
                # Delete response and message after 3 seconds
                await del_message(3, error_response, message)
                return
                
   
            
            await state.set_state(AddTaskStates.waiting_for_title)
  
    except Exception:
        # Log unexpected error and try to notify user
        logger.exception("Unexpected error occurred")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Always close database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

@router.message(AddTaskStates.waiting_for_title, F.text == CANCEL_TEXT)
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
        cancel_msg = await message.answer(
            t("task_add_cancelled"),
            reply_markup=keyboard
        )
        # Schedule deletion of the cancel notice as well
        await del_message(3, cancel_msg)
        
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
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message")  

@router.message(AddTaskStates.waiting_for_title)
async def process_task_and_create(message: Message, state: FSMContext):
    """Ask whether user wants to add more details before creating the task."""
    try:
        data = await state.get_data()

        if not message.text:
            error = await message.answer(t("task_missing_title"))
            message_ids = data.get('message_ids', [])
            message_ids.append(error.message_id)
            await state.update_data(message_ids=message_ids)
            return

        # Store current message_id
        message_ids = data.get('message_ids', [])
        message_ids.append(message.message_id)

        # Keep the title in state until the user confirms
        await state.update_data(title=message.text, message_ids=message_ids)

        # Ask whether to add more details or submit
        prompt = await message.answer(
            t("task_add_more_prompt"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text=t("btn_add_more_details"), callback_data="task_confirm_add_more"),
                    InlineKeyboardButton(text=t("btn_submit_task"), callback_data="task_confirm_submit"),
                ]]
            ),
        )

        message_ids.append(prompt.message_id)
        await state.update_data(message_ids=message_ids)
        await state.set_state(AddTaskStates.confirming_task)

    except Exception:
        # Log unexpected error and try to notify user
        logger.exception("Unexpected error occurred")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message")


@router.callback_query(AddTaskStates.confirming_task, F.data == "task_confirm_submit")
async def handle_confirm_submit(callback_query: CallbackQuery, state: FSMContext):
    """Finalize creation without adding extra details."""
    db = None
    try:
        data = await state.get_data()
        title = data.get("title")
        if not title:
            await callback_query.answer(t("task_missing_title"), show_alert=True)
            await state.clear()
            return

        group_id = data.get("group_id")
        topic_id = data.get("topic_id")
        group_id = int(group_id) if group_id else None
        topic_id = int(topic_id) if topic_id else None

        db = next(get_db())
        task = TaskService.create_task(
            db=db,
            admin_id=data["user_id"],
            group_id=group_id,
            topic_id=topic_id,
            title=title,
        )
        if not task:
            await callback_query.answer(t("task_create_failed"), show_alert=True)
            return

        keyboard = get_main_menu_keyboard(
            chat_type=data["chat_type"],
            is_admin=data.get("user_admin", False)
        )
        await callback_query.message.answer(
            t("task_create_submit_success", title=title),
            reply_markup=keyboard
        )

        for msg_id in data.get("message_ids", []):
            try:
                await callback_query.bot.delete_message(
                    chat_id=callback_query.message.chat.id,
                    message_id=msg_id
                )
            except Exception:
                logger.exception("Failed to clean up message during submit flow")
                continue

        await state.clear()
        await callback_query.answer()

    except Exception:
        logger.exception("Unexpected error during submit confirmation")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error toast in submit confirmation")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in submit confirmation")


@router.callback_query(AddTaskStates.confirming_task, F.data == "task_confirm_add_more")
async def handle_confirm_add_more(callback_query: CallbackQuery, state: FSMContext):
    """Create task then direct user to edit template for extra details."""
    db = None
    try:
        data = await state.get_data()
        title = data.get("title")
        if not title:
            await callback_query.answer(t("task_missing_title"), show_alert=True)
            await state.clear()
            return

        group_id = data.get("group_id")
        topic_id = data.get("topic_id")
        group_id = int(group_id) if group_id else None
        topic_id = int(topic_id) if topic_id else None

        db = next(get_db())
        task = TaskService.create_task(
            db=db,
            admin_id=data["user_id"],
            group_id=group_id,
            topic_id=topic_id,
            title=title,
        )
        if not task:
            await callback_query.answer(t("task_create_failed"), show_alert=True)
            return

        for msg_id in data.get("message_ids", []):
            if msg_id == callback_query.message.message_id:
                continue
            try:
                await callback_query.bot.delete_message(
                    chat_id=callback_query.message.chat.id,
                    message_id=msg_id
                )
            except Exception:
                logger.exception("Failed to delete message in add-more flow")
                continue

        await callback_query.message.edit_text(
            t("task_create_more_success", title=title),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=t("btn_open_task_template"), callback_data=f"view_task|{task.id}")],
                    [InlineKeyboardButton(text=t("btn_finish_creation"), callback_data="back_to_main_menu")],
                ]
            )
        )

        await state.clear()
        await callback_query.answer()

    except Exception:
        logger.exception("Unexpected error during add-more confirmation")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error toast in add-more confirmation")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in add-more confirmation")
