from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.filters import Command
from .. import admin_require, del_message, get_callback, chat_type_filter
from .. import main_router as router
from database import get_db
from aiogram.enums import ChatType
from logger import logger
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from services.task_services import TaskService, TaskAttachmentService
from services.user_services import UserService
from typing import Tuple, List
from ..funcs import exception_decorator
from aiogram import F
import asyncio
import datetime
from aiogram.exceptions import TelegramBadRequest
import re
import uuid
from config import config
from utils.date_utils import gregorian_to_jalali, jalali_to_gregorian
from utils.texts import t

media_cache = {}


async def _send_attachment_notification(callback_obj, task, attachment_id, added_by_admin: bool):
    """Send only the new attachment to relevant users."""
    db = next(get_db())
    try:
        recipients = []
        admin_user = UserService.get_user(db=db, user_ID=task.admin_id)
        assigned_users = TaskService.get_task_users(db=db, task_id=task.id) or []

        if added_by_admin:
            recipients = [u for u in assigned_users if u.telegram_id]
        else:
            if admin_user and admin_user.telegram_id:
                recipients = [admin_user]

        if not recipients:
            return

        # Send the new attachment itself, not previous ones
        for user in recipients:
            try:
                text_msg = t("notify_attachment_to_user", title=task.title) if added_by_admin else t("notify_attachment_to_admin", title=task.title)
                if attachment_id.startswith("AgAC"):
                    await callback_obj.bot.send_photo(chat_id=user.telegram_id, photo=attachment_id, caption=text_msg)
                else:
                    await callback_obj.bot.send_document(chat_id=user.telegram_id, document=attachment_id, caption=text_msg)
            except Exception:
                logger.exception("Failed to send attachment notification")
                continue
    finally:
        try:
            db.close()
        except Exception:
            logger.exception("Failed to close db in notification helper")


@exception_decorator
def chunk_list(lst:list, chunk_size: int) -> List[List] | None:
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def task_manage_keyboard(db) -> Tuple[List[str], InlineKeyboardMarkup]:
    text = []
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    groups = TaskService.get_all_groups(db=db)
    if groups is None:
        text.append("⚠️ هیچ گروهی برای نمایش وجود ندارد ⚠️")
        tasks = TaskService.get_all_tasks(db=db)
        if tasks is None:
            text.append("⚠️ هیچ تسکی برای نمایش وجود ندارد ⚠️")
        else:
            text.append(f"تسک ها : \n تعداد: {len(groups)}")
            keyboard.inline_keyboard.extend(
                [
                    [InlineKeyboardButton(text=b.title, callback_data=f"view_task|{b.id}") for b in c]
                    for c in chunk_list(tasks, 2)
                ]
            )
    
    else:
        text.append(f"گروه ها : \n تعداد: {len(groups)}")
        keyboard.inline_keyboard.extend(
            [
                [InlineKeyboardButton(text=b.name, callback_data=f"view_group|{b.id}") for b in c]
                for c in chunk_list(groups, 2)
            ]
        )
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="سایر ...", callback_data=f"view_group|OTHER")]
        )

    return text, keyboard


# ===== Handler for show group's tasks =====
@router.callback_query(F.data.startswith("view_group|"))
async def handle_view_group_tasks(callback_query: CallbackQuery):
    db = None
    try:
        # Get database session
        db = next(get_db())

        # Extract group's ID
        try:
            group_ID = callback_query.data.split("|")[1]
            if group_ID != "OTHER":
                group_ID = int(group_ID)
                group = TaskService.get_group(db=db, id=group_ID)
                if group is None:
                    await callback_query.answer("❌ مشکلی در پیدا کردن این گروه به وجود آمد")
            else:
                group = None
                group_ID = False
        except Exception:
            logger.exception("Failed to extract group's ID from callback_query")
            await callback_query.answer("❌ مشکلی در پیدا کردن این گروه به وجود آمد")
            return
        
        topics = None
        tasks = None
        if group:
            topics = TaskService.get_all_topics(db=db, group_id=group.id)
        if topics and len(topics) > 0:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            keyboard.inline_keyboard.extend(
                [
                    [InlineKeyboardButton(text=b.name, callback_data=f"view_topic|{b.id}") for b in c]
                    for c in chunk_list(topics, 2)
                ]
            )
            keyboard.inline_keyboard.append(
                [
                    InlineKeyboardButton(text="باز گشت 🔙", callback_data="back"),
                    InlineKeyboardButton(text="سایر ...", callback_data=f"view_topic|OTHER|{group.id}"),
                ]
            )
        
        else:
            tasks = TaskService.get_all_tasks(db=db, group_id=group_ID)
            if not tasks:
                await callback_query.answer("⚠️ تسکی برای این گروه پیدا نشد ⚠️")
                return
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            keyboard.inline_keyboard.extend(
                [
                    [InlineKeyboardButton(text=b.title, callback_data=f"view_task|{b.id}") for b in c]
                    for c in chunk_list(tasks, 2)
                ]
            )
            keyboard.inline_keyboard.append(
                [InlineKeyboardButton(text="باز گشت 🔙", callback_data="back")]
            )
            
        await callback_query.message.edit_text(
            f"{"تسک" if tasks else "تاپیک"}{f"های گروه {group.name}" if group else " های سایر"}: \n"
            f"تعداد: {len(tasks) if tasks else len(topics)}\n\n",
            reply_markup=keyboard
        )
    
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


# ===== Handler for show topic's tasks =====
@router.callback_query(F.data.startswith("view_topic|"))
async def handle_view_topic_tasks(callback_query: CallbackQuery):
    db = None
    try:
        # Get database session
        db = next(get_db())

        # Extract topic's ID
        try:
            topic_ID = callback_query.data.split("|")[1]
            if topic_ID == "OTHER":
                group_ID = int(callback_query.data.split("|")[2])
                topic = None
                topic_ID = False
        except Exception:
            logger.exception("Failed to extract topic's ID from callback_query")
            await callback_query.answer("❌ مشکلی در پیدا کردن این تاپیک به وجود آمد")
            return

        if topic_ID == False:
            tasks = TaskService.get_all_tasks(db=db, topic_id=topic_ID, group_id=group_ID)
        else:
            tasks = TaskService.get_all_tasks(db=db, topic_id=topic_ID)
        if not tasks:
            await callback_query.answer("⚠️ تسکی برای این تاپیک پیدا نشد ⚠️")
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        keyboard.inline_keyboard.extend(
            [
                [InlineKeyboardButton(text=b.title, callback_data=f"view_task|{b.id}") for b in c]
                for c in chunk_list(tasks, 2)
            ]
        )
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="باز گشت 🔙", callback_data="back")]
        )
            
        await callback_query.message.edit_text(
            f"تسک ها\n"
            f"تعداد: {len(tasks)}\n\n",
            reply_markup=keyboard
        )
        await callback_query.answer()
    
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



# ===== Handler for finish manage =====
@router.callback_query(F.data.startswith("finish_task_manage|"))
async def handle_view_group_tasks(callback_query: CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")   


# ===== Handler for manage tasks =====
@router.callback_query(F.data == "back")
@router.message(Command("tasks"))
async def handle_task_manage(event: Message | CallbackQuery):
    """Main handler for manage tasks"""
    db = None
    try:
        db = next(get_db())  # Open a database session        
        # Check admin permission before proceeding
        permission = await admin_require(db, event)
        if not permission:
            return
        
        text, keyboard = task_manage_keyboard(db=db)

        text="\n".join(text)
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text=text, reply_markup=keyboard)
        else:
            await event.answer(text=text, reply_markup=keyboard)
            await event.delete()
             
    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            if isinstance(event, CallbackQuery):
                await event.message.edit_text(text=text, reply_markup=keyboard)
            else:
                await event.answer(text=text, reply_markup=keyboard)
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Always close the database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

# ====== Task View Menu ======
@router.callback_query(F.data.startswith("view_task|"))
@router.callback_query(F.data.startswith("show_task|"))
async def handle_view_task(callback_query: CallbackQuery, state: FSMContext = None):
    """Handle view task callback"""
    db = None
    try:
        show_type = callback_query.data.split("|")[0]
        task_id = int(callback_query.data.split("|")[1])
        if state:
            await state.clear()

        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)

        if not task:
            await callback_query.answer(t("task_not_found"))
            return

        admin = UserService.get_user(db, user_ID=task.admin_id)
        group = TaskService.get_group(db, task.group_id)
        topic = TaskService.get_topic(db=db, id=task.topic_id)
        current_user = UserService.get_user(db=db, user_tID=str(callback_query.from_user.id))
        is_admin = bool(current_user and current_user.is_admin)
        is_assigned = bool(current_user and TaskService.is_user_assigned(db=db, task_id=task_id, user_id=current_user.id))

        if not admin:
            await callback_query.answer(t("admin_not_found"))
            return

        if not is_admin and not is_assigned and show_type != "view_task":
            await callback_query.answer(t("access_denied_task"), show_alert=True)
            return

        if is_admin and show_type == "view_task":
            keyboard_buttons = [
                [InlineKeyboardButton(text=t("btn_back"), callback_data="back"), InlineKeyboardButton(text=t("btn_delete_task"), callback_data=f"delete_task|{task.id}")],
                [InlineKeyboardButton(text=t("btn_add_user"), callback_data=f"add_user|{task.id}"), InlineKeyboardButton(text=t("btn_remove_users"), callback_data=f"del_users|{task.id}")],
                [InlineKeyboardButton(text=t("btn_view_assigned"), callback_data=f"view_task_users|{task.id}"), InlineKeyboardButton(text=t("btn_set_deadline"), callback_data=f"edit_end|{task.id}")],
                [InlineKeyboardButton(text=t("btn_edit_desc"), callback_data=f"edit_desc|{task.id}"), InlineKeyboardButton(text=t("btn_edit_name"), callback_data=f"edit_name|{task.id}")],
                [InlineKeyboardButton(text=t("btn_add_attachment"), callback_data=f"add_attachment|{task.id}"), InlineKeyboardButton(text=t("btn_get_attachments"), callback_data=f"get_attachments|{task.id}")],
                [InlineKeyboardButton(text=t("btn_update_status"), callback_data=f"choose_status|{task.id}|{show_type}")],
            ]
        else:
            back_cb = "back_show" if show_type == "show_task" else "back"
            keyboard_buttons = [
                [InlineKeyboardButton(text=t("btn_back"), callback_data=back_cb)],
                [InlineKeyboardButton(text=t("btn_add_attachment"), callback_data=f"add_attachment|{task.id}"), InlineKeyboardButton(text=t("btn_get_attachments"), callback_data=f"get_attachments|{task.id}")],
                [InlineKeyboardButton(text=t("btn_update_status"), callback_data=f"choose_status|{task.id}|{show_type}")],
            ]

        inline_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        assigned_users = TaskService.get_task_users(db=db, task_id=task_id)
        if assigned_users:
            users_text = t("assigned_users_title") + "\n\n" + "\n".join(f"{i}. {u.username}" for i, u in enumerate(assigned_users, 1))
        else:
            users_text = t("assigned_users_none")

        start_jalali = gregorian_to_jalali(task.start_date)
        end_jalali = gregorian_to_jalali(task.end_date)

        group_line = f"Group: {group.name}\n" if group else ""
        topic_line = f"Topic: {topic.name} - {topic.link}\n" if topic else ""
        desc = task.description or t("not_set")
        body = t(
            "task_view_template",
            title=task.title,
            admin=admin.username,
            group_line=group_line,
            topic_line=topic_line,
            description=desc,
            start=start_jalali,
            end=end_jalali,
            status=task.status,
            users=users_text,
        )

        await callback_query.message.edit_text(body, reply_markup=inline_keyboard)
        await callback_query.answer()

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message")

    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")


STATUS_CHOICES = [
    ("pending", "Pending"),
    ("in_progress", "In progress"),
    ("done", "Done"),
    ("blocked", "Blocked"),
]


@router.callback_query(F.data.startswith("choose_status|"))
async def handle_choose_status(callback_query: CallbackQuery):
    """Show status options for admins or assigned users."""
    db = None
    try:
        parts = callback_query.data.split("|")
        task_id = int(parts[1])
        show_type = parts[2] if len(parts) > 2 else "view_task"

        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        user = UserService.get_user(db=db, user_tID=str(callback_query.from_user.id))
        is_admin = bool(user and user.is_admin)
        is_assigned = bool(user and TaskService.is_user_assigned(db=db, task_id=task_id, user_id=user.id)) if user else False

        if not task:
            await callback_query.answer(t("task_not_found"))
            return
        if not is_admin and not is_assigned:
            await callback_query.answer(t("status_update_forbidden"), show_alert=True)
            return

        keyboard_rows = []
        for i in range(0, len(STATUS_CHOICES), 2):
            row = []
            for status_value, status_label in STATUS_CHOICES[i:i+2]:
                row.append(
                    InlineKeyboardButton(
                        text=status_label,
                        callback_data=f"change_status|{task_id}|{status_value}|{show_type}"
                    )
                )
            keyboard_rows.append(row)

        keyboard_rows.append([InlineKeyboardButton(text=t("btn_cancel"), callback_data=f"{show_type}|{task_id}")])

        await callback_query.message.answer(
            t("choose_status_prompt"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        )
        await callback_query.answer()

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")


@router.callback_query(F.data.startswith("change_status|"))
async def handle_change_status(callback_query: CallbackQuery):
    """Apply a new status to a task."""
    db = None
    try:
        parts = callback_query.data.split("|")
        task_id = int(parts[1])
        new_status = parts[2]
        show_type = parts[3] if len(parts) > 3 else "view_task"

        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        user = UserService.get_user(db=db, user_tID=str(callback_query.from_user.id))
        is_admin = bool(user and user.is_admin)
        is_assigned = bool(user and TaskService.is_user_assigned(db=db, task_id=task_id, user_id=user.id)) if user else False

        if not task:
            await callback_query.answer(t("task_not_found"))
            return
        if not is_admin and not is_assigned:
            await callback_query.answer(t("status_update_forbidden"), show_alert=True)
            return

        res = TaskService.update_status(db=db, task_id=task_id, status=new_status)
        if res == "NOT_EXIST":
            await callback_query.answer(t("task_not_found"))
            return
        if not res:
            await callback_query.answer(t("status_update_failed"))
            return

        await callback_query.answer(t("status_updated"))
        try:
            await callback_query.message.delete()
        except Exception:
            logger.exception("Failed to delete status selection message")
        # Refresh the task view
        target = "show_task" if show_type == "show_task" else "view_task"
        mock_callback = get_callback(callback_query, f"{target}|{task_id}")
        await handle_view_task(mock_callback)

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("Unexpected error occurred")
        except Exception:
            logger.exception("Failed to send error message")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

# ====== Delete Task ======
@router.callback_query(F.data.startswith("delete_task|"))
async def handle_delete_task(callback_query: CallbackQuery):
    """Handle delete task callback"""
    db = None
    try:
        task_id = int(callback_query.data.split("|")[1])

        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        
        if not task:
            await callback_query.answer("❌ تسک یافت نشد")
            # Return to task list if task not found
            await handle_task_manage(callback_query)
            return
        
        task_title = task.title
        
        # Delete task
        res = TaskService.delete_task(db=db, task=task)
        if not res:
            await callback_query.answer("❌ خطا در حذف تسک")

        await callback_query.answer(f"✅ حذف شد {task_title} تسک")
        
        # Return to task list
        await handle_task_manage(callback_query)
        
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


# ====== Edit Task States ======
class EditTaskStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_desc = State()
    waiting_for_end = State()


# ====== Edit Task Name ======
@router.callback_query(F.data.startswith("edit_name|"))
async def handle_edit_name(callback_query: CallbackQuery, state: FSMContext):
    db = None
    try:
        task_id = int(callback_query.data.split("|")[1])
        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)

        if not task:
            await callback_query.answer("❌ تسک یافت نشد")
            return
        
        await state.update_data(task_id=task_id, prompt_msg_id=callback_query.message.message_id)

        await state.set_state(EditTaskStates.waiting_for_name)
        
        await callback_query.message.edit_text(
            f"📝 تغییر نام تسک: {task.title}\n\n"
            "لطفاً نام جدید را وارد کنید:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 بازگشت",
                        callback_data=f"view_task|{task_id}"
                    )
                ]]
            )
        )
        await callback_query.answer()

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

@router.message(EditTaskStates.waiting_for_name)
async def process_edit_name(message: Message, state: FSMContext):
    db = None
    try:
        new_name = message.text.strip()
        data = await state.get_data()
        task_id = int(data.get("task_id"))
        prompt_msg_id = data.get("prompt_msg_id")

        db = next(get_db())
        res = TaskService.edit_task(db=db, task_id=task_id, name=new_name)

        # پیام کاربر پاک بشه
        await message.delete()

        if res:
            text = "✅ نام تسک تغییر کرد"
        elif res == "NOT_EXIST":
            text = "❌ این تسک وجود ندارد"
        else:
            text = "❌ مشکلی در تغییر تسک به وجود آمد"

        # پیام اصلی که قبلاً ذخیره کردیم تغییر کنه
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=prompt_msg_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 بازگشت به تسک",
                        callback_data=f"view_task|{task_id}"
                    )
                ]]
            )
        )
        
        await state.clear()

    except Exception:
        logger.exception("Unexpected error occurred")
        data = await state.get_data()
        task_id = data.get("task_id", "UNKNOWN")
        prompt_msg_id = data.get("prompt_msg_id", message.message_id - 1)

        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text="❌ مشکلی در تغییر تسک به وجود آمد",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(
                            text="🔙 بازگشت به تسک",
                            callback_data=f"view_task|{task_id}"
                        )
                    ]]
                )
            )
        except Exception:
            logger.exception(f"Failed to send error message")
    
    finally:
        # Always close the database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

# ====== Edit Task Description ======
@router.callback_query(F.data.startswith("edit_desc|"))
async def handle_edit_desc(callback_query: CallbackQuery, state: FSMContext):
    db = None
    try:
        task_id = int(callback_query.data.split("|")[1])
        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)

        if not task:
            await callback_query.answer("❌ تسک یافت نشد")
            return
        
        await state.update_data(task_id=task_id, prompt_msg_id=callback_query.message.message_id)

        await state.set_state(EditTaskStates.waiting_for_desc)
        
        await callback_query.message.edit_text(
            f"📝 تغییر توضیحات تسک: {task.title}\n\n"
            "لطفاً توضیحات جدید را وارد کنید:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 بازگشت",
                        callback_data=f"view_task|{task_id}"
                    )
                ]]
            )
        )
        await callback_query.answer()

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

@router.message(EditTaskStates.waiting_for_desc)
async def process_edit_desc(message: Message, state: FSMContext):
    db = None
    try:
        new_des = message.text.strip()
        data = await state.get_data()
        task_id = int(data.get("task_id"))
        prompt_msg_id = data.get("prompt_msg_id")

        db = next(get_db())
        res = TaskService.edit_task(db=db, task_id=task_id, description=new_des)

        # پیام کاربر پاک بشه
        await message.delete()

        if res:
            text = "✅ توضیحات تسک تغییر کرد"
        elif res == "NOT_EXIST":
            text = "❌ این تسک وجود ندارد"
        else:
            text = "❌ مشکلی در تغییر تسک به وجود آمد"

        # پیام اصلی که قبلاً ذخیره کردیم تغییر کنه
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=prompt_msg_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 بازگشت به تسک",
                        callback_data=f"view_task|{task_id}"
                    )
                ]]
            )
        )
        
        await state.clear()

    except Exception:
        logger.exception("Unexpected error occurred")
        data = await state.get_data()
        task_id = data.get("task_id", "UNKNOWN")
        prompt_msg_id = data.get("prompt_msg_id", message.message_id - 1)

        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text="❌ مشکلی در تغییر تسک به وجود آمد",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(
                            text="🔙 بازگشت به تسک",
                            callback_data=f"view_task|{task_id}"
                        )
                    ]]
                )
            )
        except Exception:
            logger.exception(f"Failed to send error message")
    
    finally:
        # Always close the database connection
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")



# ====== Edit Task End Date ======
@router.callback_query(F.data.startswith("edit_end|"))
async def handle_edit_end(callback_query: CallbackQuery, state: FSMContext):
    """
    When user clicks 'edit_end|<task_id>', ask them for a new end date.
    """
    db = None
    try:
        # Extract task_id from callback data
        task_id = int(callback_query.data.split("|")[1])

        # Open DB session
        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)

        # If task does not exist
        if not task:
            await callback_query.answer(t("task_not_found"))
            return
        
        # Save task_id and the message_id of the bot's message into FSM state
        await state.update_data(
            task_id=task_id,
            callback_message_id=callback_query.message.message_id
        )

        # Set FSM state to wait for user input (new end date)
        await state.set_state(EditTaskStates.waiting_for_end)
        
        # Ask user to enter new date
        await callback_query.message.edit_text(
            t("deadline_prompt", title=task.title),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text=t("btn_back"),
                        callback_data=f"view_task|{task_id}"
                    )
                ]]
            )
        )
        await callback_query.answer()

    except Exception:
        # Log and notify user if unexpected error happens
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message") 
    
    finally:
        # Always close DB session
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close DB session in handle_edit_end")

# ====== Process new end date ======
@router.message(EditTaskStates.waiting_for_end)
async def process_edit_end(message: Message, state: FSMContext):
    """
    Process user input (new end date) and update the task in DB.
    """
    db = None
    try:
        # Get text input from user
        date_text = message.text.strip()

        # Retrieve state data
        data = await state.get_data()
        task_id = int(data.get("task_id"))
        callback_message_id = data.get("callback_message_id")
        prev_error_msg_id = data.get("error_message_id")

        # Try to parse Jalali date (YYYY-MM-DD)
        new_end = jalali_to_gregorian(date_text)
        if not new_end:
            # Delete user's wrong message
            try:
                await message.delete()
            except Exception:
                logger.exception("Could not delete invalid date message")

            # Delete previous error message (if exists)
            if prev_error_msg_id:
                try:
                    await message.bot.delete_message(
                        chat_id=message.chat.id,
                        message_id=prev_error_msg_id
                    )
                except Exception:
                    logger.exception("Could not delete previous error message")

            # Send new error message and store its id
            try:
                err_msg = await message.answer(
                    t("deadline_invalid_format")
                )
                await state.update_data(error_message_id=err_msg.message_id)
            except Exception:
                logger.exception("Could not send new error message")

            return

        # Update task in DB
        db = next(get_db())
        res = TaskService.edit_task(
            db=db,
            task_id=task_id,
            end_date=new_end
        )

        # Decide response text based on result
        if res:
            text = t("deadline_update_success")
        elif res == "NOT_EXIST":
            text = t("deadline_update_not_exist")
        else:
            text = t("deadline_update_failed")


        # Delete user's message (the date they typed)
        try:
            await message.delete()
        except Exception:
            logger.exception("Could not delete user message after success")

        # Delete old error message if any
        if prev_error_msg_id:
            try:
                await message.bot.delete_message(
                    chat_id=message.chat.id,
                    message_id=prev_error_msg_id
                )
            except Exception:
                logger.exception("Could not delete previous error message after success")

        # Edit the original bot message with success info + back button
        if callback_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=callback_message_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[
                            InlineKeyboardButton(
                                text="🔙 باز گشت",
                                callback_data=f"view_task|{task_id}"
                            )
                        ]]
                    )
                )
            except Exception:
                # If editing fails, send a new message as fallback
                logger.exception("Could not edit original message, sending fallback")
                try:
                    await message.answer(
                        text,
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[[
                                InlineKeyboardButton(
                                    text="🔙 باز گشت",
                                    callback_data=f"view_task|{task_id}"
                                )
                            ]]
                        )
                    )
                except Exception:
                    logger.exception("Could not send fallback success message")

        # Clear FSM state
        await state.clear()

    except Exception:
        # Log and notify user if unexpected error happens
        logger.exception("Unexpected error occurred in process_edit_end")
        try:
            await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message in process_edit_end") 
    
    finally:
        # Always close DB session
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close DB session in process_edit_end")


# ====== Add User to Task ======
@router.callback_query(F.data.startswith("add_user|"))
async def handle_add_user(callback_query: CallbackQuery, state: FSMContext):
    """Handle add user to task callback"""   
    db = None 
    try:
        task_id = int(callback_query.data.split("|")[1])

        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        
        if not task:
            await callback_query.answer("❌ تسک یافت نشد")
            return
        
        # Store task info in state
        await state.update_data(
            task_id=task_id,
            callback_message_id=callback_query.message.message_id
        )
        
        # Get suggested users from database
        suggested_users = list(UserService.get_all_users(db, user_tID=callback_query.from_user.id, task_id=task_id))
        if len(suggested_users) == 0:
            await callback_query.answer("⚠️ کاربری برای نمایش وجود ندارد ⚠️")
            return
        suggested_users = [user.username for user in suggested_users]
            
        
        # Create inline keyboard with suggested users
        keyboard_buttons = []
        
        # Add suggested users as buttons
        if len(list(suggested_users)) != 0:
            for username in suggested_users:
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"👤 {username}",
                        callback_data=f"select_user|{username}"
                    )
                ])
        
        # Add back button
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="🔙 بازگشت",
                callback_data=f"view_task|{task_id}"
            )
        ])
        
        inline_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Edit message to show user selection
        message_text = f"👥 افزودن کاربر به تسک: {task.title}\n\n"
        
        await callback_query.message.edit_text(
            message_text,
            reply_markup=inline_keyboard
        )
        
        await callback_query.answer()
        
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

@router.callback_query(F.data.startswith("select_user|"))
async def handle_select_user(callback_query: CallbackQuery, state: FSMContext):
    """Handle user selection from suggested users"""
    db = None
    try:
        username = callback_query.data.split("|")[1]

        data = await state.get_data()
        task_id_str = data.get('task_id')
        if not task_id_str:
            await callback_query.answer("❌ اطلاعات تسک یافت نشد")
            return
        task_id = int(task_id_str)
        
        if not task_id:
            await callback_query.answer("❌ اطلاعات تسک یافت نشد")
            return
        
        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        
        # Find or create user
        user = UserService.get_or_create_user(db, username=username)
        if not user:
            await callback_query.answer("❌ خطا در پیدا کردن کاربر")
                
        # Assign user to task
        res = UserService.assign_user_to_task(db, user.id, task_id)
        if not res:
            await callback_query.answer("❌  خطا در افزودن کاربر به تسک")

        try:
            bot = callback_query.bot
            if user.telegram_id:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=f"شما به تسک {task.title} اضافه شدید",
                    reply_markup = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="دیدن تسک", callback_data=f"show_task|{task_id}")]
                        ]
                    )
                )
        except Exception:
            logger.exception("Failed to send notification message to user")

        await callback_query.answer(f"✅ کاربر @{username} اضافه شد")
        
        # Clear state
        await state.clear()
        
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


# ====== View Task Users ======
@router.callback_query(F.data.startswith("view_task_users|"))
async def handle_view_task_users(callback_query: CallbackQuery):
    """Handle view task users callback - display users assigned to a task"""    
    db = None
    try:
        task_id = int(callback_query.data.split("|")[1])

        db = next(get_db())
        
        # Get task information
        task = TaskService.get_task_by_id(db=db, id=task_id)
        if not task:
            await callback_query.answer("❌ تسک یافت نشد")
            return
        
        # Get all users assigned to this task
        assigned_users = TaskService.get_task_users(db=db, task_id=task_id)

        admin_user = UserService.get_user(db=db, user_ID=task.admin_id)
        if admin_user:
            task_admin_username = admin_user.username
        else:
            task_admin_username = "نامشخص"
        
        # Create message text
        if assigned_users:
            users_text = "👥 کاربران اختصاص داده شده به این تسک:\n\n"
            for i, user in enumerate(assigned_users, 1):
                users_text += f"{i}. {user.username}\n"
        else:
            users_text = "📝 هیچ کاربری به این تسک اختصاص داده نشده است."
        
        # Create back button
        keyboard_buttons = [
            [InlineKeyboardButton(text="🔙 بازگشت به تسک", callback_data=f"view_task|{task_id}")]
        ]
        
        inline_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Edit message to show users
        await callback_query.message.edit_text(
            f"📋 تسک: {task.title}\n\n"
            f"ادمین : {task_admin_username}\n"
            f"{users_text}",
            reply_markup=inline_keyboard
        )
        
        await callback_query.answer()
        
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


# ====== Delete User States ======
class DeleteUserStates(StatesGroup):
    waiting_for_user_selection = State()  # State for waiting until a user is selected for deletion


# ====== Delete User from Task ======
@router.callback_query(F.data.startswith("del_users|"))
async def handle_delete_user_menu(callback_query: CallbackQuery, state: FSMContext):
    """Handle delete user from task menu callback"""
    db = None
    try:
        # Extract task ID from callback data
        task_id = int(callback_query.data.split("|")[1])

        # Open a database session
        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        
        if not task:
            # Task not found
            await callback_query.answer("❌ تسک یافت نشد")
            return
        
        # Get all users assigned to this task
        assigned_users = TaskService.get_task_users(db=db, task_id=task_id)
        
        if not assigned_users:
            # No users to delete
            await callback_query.answer("⚠️ هیچ کاربری در این تسک وجود ندارد")
            return
        
        # Store task info and assigned users in FSM state
        await state.update_data(
            task_id=task_id,
            callback_message_id=callback_query.message.message_id,
            assigned_users=[user.id for user in assigned_users]
        )
        
        # Create inline keyboard for user deletion
        keyboard_buttons = []
        
        # Add a button for each assigned user (limit 10 to avoid too many buttons)
        for user in assigned_users[:10]:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"❌ حذف {user.username}",
                    callback_data=f"delete_user_final|{user.id}"
                )
            ])
        
        # Add a back button to return to task view
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="🔙 بازگشت",
                callback_data=f"view_task|{task_id}"
            )
        ])
        
        inline_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Edit the message to show the user selection menu
        try:
            await callback_query.message.edit_text(
                f"🗑️ حذف کاربر از تسک: {task.title}\n\n"
                "لطفاً کاربری که می‌خواهید حذف کنید را انتخاب کنید:",
                reply_markup=inline_keyboard
            )
        except TelegramBadRequest:
            # Telegram throws this if message content is unchanged
            pass
        except Exception:
            try:
                await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            except Exception:
                logger.exception("Failed to send error message")
        
    except Exception:
        # Log any unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Always close the database session
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

# ====== Final User Deletion ======
@router.callback_query(F.data.startswith("delete_user_final|"))
async def handle_delete_user_final(callback_query: CallbackQuery, state: FSMContext):
    """Handle final deletion of a selected user from a task"""
    user_id_to_delete = int(callback_query.data.split("|")[1])
    db = None
    try:
        # Get stored FSM state data
        data = await state.get_data()
        task_id = int(data.get('task_id'))
        
        if not task_id:
            await callback_query.answer("❌ اطلاعات تسک یافت نشد")
            return
        
        # Open a database session
        db = next(get_db())
        
        # Fetch user and task info for display
        user_to_delete = UserService.get_user(db=db, user_ID=user_id_to_delete)
        task = TaskService.get_task_by_id(db=db, id=task_id)
        
        if not user_to_delete or not task:
            await callback_query.answer("❌ اطلاعات یافت نشد")
            return
        
        # Attempt to delete the user from the task
        res = TaskService.delete_user_from_task(db=db, task_id=task_id, user_id=user_id_to_delete)

        # Prepare a mock callback for returning to task view
        mock_callback = get_callback(callback_query, f"view_task|{task_id}")
        
        if res:
            # Successful deletion
            await callback_query.answer(f"✅ کاربر @{user_to_delete.username} حذف شد")
            
            # Call view task handler to refresh the view
            await handle_view_task(mock_callback)

            # Clear FSM state
            await state.clear()
        
        elif res == "NOT_EXIST":
            # User was not part of this task
            await callback_query.answer("❌ کاربر در این تسک وجود ندارد")

            # Refresh task view
            await handle_view_task(mock_callback)

            # Clear FSM state
            await state.clear()
            return
            
        else:
            # Any other failure
            await callback_query.answer("❌ خطا در حذف کاربر")

            # Refresh task view
            await handle_view_task(mock_callback)

            # Clear FSM state
            await state.clear()
            return
            
    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Always close the database session
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")            




# ====== Add Attachments to Task ======
@router.callback_query(F.data.startswith("add_attachment"))
async def handle_add_attachment(callback_query: CallbackQuery, state: FSMContext):
    """
    Start attachment adding mode for a task.
    After this, any file or supported message type will be stored as attachment.
    """
    db = None
    try:
        # Extract task_id from callback_data
        # Format example: add_attachment|<task_id>
        task_id = int(callback_query.data.split("|")[1])

        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        user = UserService.get_user(db=db, user_tID=str(callback_query.from_user.id))
        is_admin = bool(user and user.is_admin)
        is_assigned = bool(user and TaskService.is_user_assigned(db=db, task_id=task_id, user_id=user.id)) if user else False

        if not task:
            await callback_query.answer(t("task_not_found"))
            return
        if not is_admin and not is_assigned:
            await callback_query.answer(t("attachments_add_forbidden"), show_alert=True)
            return

        # Store in state that we are adding attachments for this task
        await state.update_data(
            task_id=task_id,
            adding_attachments=True
        )

        # Edit current message to notify user
        await callback_query.message.edit_text(
            t("attachments_add_prompt"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text=t("btn_back"),
                        callback_data=f"view_task|{task_id}"
                    )
                ]]
            )
        )

        # Answer callback to remove "loading" state
        await callback_query.answer()

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send callback error message")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")


@router.message(F.document | F.photo | F.video | F.audio | F.voice)
async def handle_new_attachment(message: Message, state: FSMContext):
    """
    Handle any new messages or files as attachments if the user is in 'adding_attachments' mode.
    """
    db = None
    try:
        data = await state.get_data()
        adding_attachments = data.get("adding_attachments", False)
        task_id = data.get("task_id")

        if not adding_attachments or not task_id:
            # Not in attachment adding mode
            return

        db = next(get_db())

        attachment_id = None

        # Determine attachment type
        if message.document:
            attachment_id = message.document.file_id
        elif message.photo:
            # For photo, take the highest resolution
            attachment_id = message.photo[-1].file_id
        elif message.video:
            attachment_id = message.video.file_id
        elif message.audio:
            attachment_id = message.audio.file_id
        elif message.voice:
            attachment_id = message.voice.file_id
        else:
            # You can extend for other message types
            return

        # Save attachment to database
        added = TaskAttachmentService.add_attachment(db=db, task_id=task_id, attachment_id=attachment_id)

        # Optionally notify user
        if added:
            msg = await message.answer(t("attachments_added"))
            await del_message(3, msg)
            # Notify admin or assigned users
            task = TaskService.get_task_by_id(db=db, id=task_id)
            adder_user = UserService.get_user(db=db, user_tID=str(message.from_user.id))
            added_by_admin = bool(adder_user and adder_user.is_admin)
            await _send_attachment_notification(message, task, attachment_id, added_by_admin)

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await message.answer(t("attachments_add_error"))
        except Exception:
            logger.exception("Failed to send error message")
    
    finally:
        if db:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close DB session")


# ====== Send Attachments of Task to User ======
@router.callback_query(F.data.startswith("get_attachments|"))
async def handle_get_attachments(callback_query: CallbackQuery):
    """
    Send all attachments of a task to the same chat without editing the original message.
    Detect attachment type and use appropriate send method.
    """
    db = None
    try:
        task_id = int(callback_query.data.split("|")[1])
        db = next(get_db())

        task = TaskService.get_task_by_id(db=db, id=task_id)
        user = UserService.get_user(db=db, user_tID=str(callback_query.from_user.id))
        is_admin = bool(user and user.is_admin)
        is_assigned = bool(user and TaskService.is_user_assigned(db=db, task_id=task_id, user_id=user.id)) if user else False

        if not task:
            await callback_query.answer(t("task_not_found"))
            return
        if not is_admin and not is_assigned:
            await callback_query.answer(t("attachments_view_forbidden"), show_alert=True)
            return

        # Get all attachments for the task
        attachments = TaskAttachmentService.get_attachments(db=db, task_id=task_id)

        if not attachments:
            await callback_query.answer(t("attachments_none"), show_alert=True)
            return

        # Send each attachment using the correct method
        for attachment in attachments:
            file_id = attachment

            # Simple detection based on file_id prefix (Telegram file_id conventions)
            if file_id.startswith("AgAC"):  # likely photo/video (depends on your storage)
                await callback_query.message.bot.send_photo(
                    chat_id=callback_query.message.chat.id,
                    photo=file_id
                )
            else:
                await callback_query.message.bot.send_document(
                    chat_id=callback_query.message.chat.id,
                    document=file_id
                )

        await callback_query.answer(t("attachments_sent"), show_alert=True)

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error message")
    
    finally:
        if db:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close DB session")


# ====== Show user's tasks ======
async def handle_my_tasks(event: Message | CallbackQuery):
    """
    Show all tasks assigned to the current user with inline buttons.
    """
    db = None
    try:
        # Assuming telegram_id is unique for users
        telegram_id = event.from_user.id
        db = next(get_db())

        # Get the User object
        user = UserService.get_user(db=db, user_tID=telegram_id)
        if not user:
            if isinstance(event, CallbackQuery):
                await event.answer("⚠️ شما در سیستم ثبت نشده‌اید", show_alert=True)
            else:
                em = await event.answer("⚠️ شما در سیستم ثبت نشده‌اید")
                await del_message(3, em)
            return

        # Get tasks assigned to this user
        tasks = TaskService.get_tasks_for_user(db=db, user_id=user.id)
        if not tasks:
            if isinstance(event, CallbackQuery):
                await event.answer("⚠️ هیچ تسکی برای شما وجود ندارد", show_alert=True)
            else:
                em = await event.answer("⚠️ هیچ تسکی برای شما وجود ندارد")
                await del_message(3, em)
            return

        # Build inline keyboard
        keyboard_buttons = []
        for task in tasks:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=task.title,
                    callback_data=f"show_task|{task.id}"
                )
            ])
        inline_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        # Send message with tasks
        if isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(
                    "📋 تسک‌های شما:",
                    reply_markup=inline_keyboard
                )
            except TelegramBadRequest:
                # Telegram throws this if message content is unchanged
                pass
            except Exception:
                try:
                    await event.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
                except Exception:
                    logger.exception("Failed to send error message")
        else:
            await event.answer(
                "📋 تسک‌های شما:",
                reply_markup=inline_keyboard
            )

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            if isinstance(event, CallbackQuery):
                await event.answer("❌ خطا در دریافت تسک‌ها", show_alert=True)
            else:
                await event.answer("⚠️ هیچ تسکی برای شما وجود ندارد")
        except Exception:
            logger.exception("Failed to send error message")
    
    finally:
        if db:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close DB session")


@router.message(F.text == "تسک های من")
async def handle_my_tasks_message(message: Message):
    await handle_my_tasks(event=message)

@router.callback_query(F.data == "back_show")
async def handle_my_tasks_callback(callback: CallbackQuery):
    await handle_my_tasks(event=callback)

# ===== Short Edit Commands Handler (Time, Attach, Des and Name commands) =====
@router.message(Command("name"))
@router.message(Command("des"))
@router.message(Command("attach"))
@router.message(Command("time"))
async def handle_short_edits(message: Message):
    db = None
    try:
        db = next(get_db())
        current_user = UserService.get_user(db=db, user_tID=str(message.from_user.id))
        is_admin = UserService.is_admin(db=db, user_tID=message.from_user.id)
        if message.text != "/attach" and not is_admin:
            await message.delete()
            em = await message.answer(t("no_permission_cmd"))
            await del_message(3, em)

        # Define command patterns with regex
        patterns = {
            # /name <any non-empty text>
            "name": re.compile(r"^/name\s+(.+)$", re.IGNORECASE),

            # /des <any non-empty text>
            "des": re.compile(r"^/des\s+(.+)$", re.IGNORECASE),

            # /time YYYY-MM-DD (validated later with datetime)
            "time": re.compile(r"^/time\s+(\d{4}-\d{2}-\d{2})$", re.IGNORECASE),

            # /attach or /atach optionally followed by a filename or argument
            # Accept both spellings and allow optional argument
            "attach": re.compile(r"^/(?:attach|atach)(?:\s+(.+))?$", re.IGNORECASE),
        }
        
        text = message.text.strip()
        callback_text = None

        # Iterate over command patterns to match the message
        for key, pattern in patterns.items():
            m = pattern.match(text)
            if not m:
                continue

            # Extract value (may be None for /attach without argument)
            value = m.group(1) if m.groups() else None

            if key == "name":
                # Name must be non-empty (regex ensures this)
                new_name = value.strip()
                callback_text = f"short_edit|name|{new_name}"

            if key == "des":
                new_desc = value.strip()
                callback_text = f"short_edit|des|{new_desc}"

            if key == "time":
                date_str = value
                parsed = jalali_to_gregorian(date_str)
                if not parsed:
                    await message.answer(t("deadline_invalid_format"))
                    return
                callback_text = f"short_edit|time|{date_str}"
         
            if key == "attach":
                # Ensure the command is a reply to a message containing media
                if not message.reply_to_message:
                    em = await message.answer("برای اضافه کردن فایل، ابتدا روی پیام حاوی فایل ریپلای کنید و سپس /attach را بفرستید.")
                    await del_message(3, em, message)
                    return

                reply_msg = message.reply_to_message
                file_ids = []

                # Collect file_ids from all possible media types in the replied message
                if reply_msg.photo:
                    file_ids.append(reply_msg.photo[-1].file_id)
                if reply_msg.video:
                    file_ids.append(reply_msg.video.file_id)
                if reply_msg.audio:
                    file_ids.append(reply_msg.audio.file_id)
                if reply_msg.voice:
                    file_ids.append(reply_msg.voice.file_id)
                if reply_msg.document:
                    file_ids.append(reply_msg.document.file_id)

                # Generate a unique key for storing these files in memory
                media_key = str(uuid.uuid4()) 
                media_cache[media_key] = file_ids

                # Set callback text for the attach operation
                callback_text = f"short_edit|attach|{media_key}"

        # If no command pattern matched, notify the user
        if not callback_text:
            em = await message.answer(t("invalid_command"))
            await del_message(3, em, message)
            return

        tasks = None

        # Fetch tasks depending on chat type and topic
        if message.chat.type in ("group", "supergroup"):
            if message.is_topic_message:
                topic = TaskService.get_topic(db=db, tID=str(message.message_thread_id))
                if not topic:
                    em = await message.answer(t("no_tasks_topic"))
                    await del_message(3, message, em)
                    return
                tasks = TaskService.get_all_tasks(db=db, topic_id=topic.id)
            else:
                group = TaskService.get_group(db=db, tID=str(message.chat.id))
                if not group:
                    em = await message.answer(t("no_tasks_group"))
                    await del_message(3, message, em)
                    return
                tasks = TaskService.get_all_tasks(db=db, group_id=group.id)
        else:
            em = await message.answer(t("only_group_command"))
            await del_message(3, em)
            return

        # Limit attachments for non-admins to tasks assigned to them
        if not is_admin and callback_text and callback_text.startswith("short_edit|attach|"):
            if current_user:
                tasks = TaskService.get_tasks_for_user(db=db, user_id=current_user.id)
            else:
                tasks = None

        # If no tasks found, notify user
        if not tasks:
            em = await message.answer(t("no_tasks_found"))
            await del_message(3, em, message)
            return
        
        # Prepare inline keyboard for selecting task
        keyboard = []
        for t in tasks:
            keyboard.append([
                InlineKeyboardButton(text=t.title, callback_data=f"{callback_text}|{t.id}")
            ])

        await message.answer(
            t("select_task_prompt"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
        await message.delete()
    
    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message")
    
    finally:
        # Ensure DB session is closed
        if db:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close DB session")

# ===== Short Edit Commands Handler (User commands) =====
@router.message(Command("user"))
async def handle_short_users_edits(message: Message):
    """A handler to add a user to a task"""
    db = None
    try:
        db = next(get_db())  # Open a database session

        # Check admin permission before proceeding
        permission = await admin_require(db=db, message=message)
        if not permission:
            return
        
        await message.delete()

        # Fetch tasks depending on chat type and topic
        if message.chat.type in ("group", "supergroup"):
            if message.is_topic_message:
                topic = TaskService.get_topic(db=db, tID=str(message.message_thread_id))
                if not topic:
                    em = await message.answer("هیچ تسکی برای این تاپیک وجود ندارد")
                    await del_message(3, message, em)
                    return
                tasks = TaskService.get_all_tasks(db=db, topic_id=topic.id)
            else:
                group = TaskService.get_group(db=db, tID=str(message.chat.id))
                if not group:
                    em = await message.answer("هیچ تسکی برای این گروه وجود ندارد")
                    await del_message(3, message, em)
                    return
                tasks = TaskService.get_all_tasks(db=db, group_id=group.id)
        else:
            em = await message.answer("❌ اجرای این دستور فقط در گروه ممکن است")
            await del_message(3, em)
            return

        # If no tasks found, notify user
        if not tasks:
            em = await message.answer("❌ هیچ تسکی پیدا نشد")
            await del_message(3, em, message)
            return
        
        # Prepare inline keyboard for selecting task
        keyboard = []
        for t in tasks:
            keyboard.append([
                InlineKeyboardButton(text=t.title, callback_data=f"add_user|{t.id}")
            ])

        await message.answer(
            "تسک مورد نظر را برای اعمال این عملیات انتحاب کنید",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )

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

# ===== Callback Handler for Short Edit =====
@router.callback_query(F.data.startswith("short_edit|"))
async def short_edit_confirm(callback_query: CallbackQuery):
    """
    Handles the callback when a user selects a task to apply a short edit.
    Triggered by inline buttons from handle_short_edits.
    Expected callback format: short_edit|<type>|<value>|<task_id>
    - <type>: name, des, time, attach
    - <value>: new value or list of file IDs for attachments
    - <task_id>: the task to apply the change to
    """
    db = None
    try:
        db = next(get_db())

        # Split the callback data to extract edit type, value, and task ID
        data_parts = callback_query.data.split("|")

        # Determine type of edit and value(s)
        edit_type = data_parts[1]
        edit_value = data_parts[2]
        task_id = int(data_parts[-1])

        if not task_id:
            await callback_query.answer("❌ Task ID missing")
            return

        # Handle changing the task's name
        if edit_type == "name":
            result = TaskService.edit_task(db=db, task_id=task_id, name=edit_value)
            success_message = f"✅ نام تسک تغییر کرد به: {edit_value}"

        # Handle changing the task's description
        elif edit_type == "des":
            result = TaskService.edit_task(db=db, task_id=task_id, description=edit_value)
            success_message = "✅ توضیحات تغییر کرد"

        # Handle changing the task's end date
        elif edit_type == "time":
            end_date = jalali_to_gregorian(edit_value)
            if not end_date:
                await callback_query.answer("❌ تاریخ نامعتبر است. فرمت درست: YYYY-MM-DD (شمسی)")
                return
            result = TaskService.edit_task(db=db, task_id=task_id, end_date=end_date)
            success_message = f"✅ تاریخ تسک تغییر کرد به : {edit_value}"

        # Handle attaching files to the task
        elif edit_type == "attach":
            result = True
            media_key = edit_value
            file_ids = media_cache.get(media_key, [])
            added_count = 0
            added_ids = []

            # Add each file ID to the task using TaskAttachmentService
            for file_id in file_ids:
                try:
                    added = TaskAttachmentService.add_attachment(db=db, task_id=task_id, attachment_id=file_id)
                    if added:
                        added_count += 1
                        added_ids.append(file_id)
                except Exception:
                    logger.exception(f"Failed to attach file {file_id} to task {task_id}")

            # Notify user if no files were added
            if added_count == 0:
                await callback_query.answer("❌ هیچ فایلی اضافه نشد")
                return

            # Inform user about successful attachments and remove cache
            success_message = f"✅ تعداد {added_count} فایل به تسک اضافه شد"
            file_ids = media_cache.__delitem__(media_key)

            # Send notifications with only new files
            task = TaskService.get_task_by_id(db=db, id=task_id)
            adder_user = UserService.get_user(db=db, user_tID=str(callback_query.from_user.id))
            added_by_admin = bool(adder_user and adder_user.is_admin)
            for fid in added_ids:
                await _send_attachment_notification(callback_query, task, fid, added_by_admin)

            view_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📋 دیدن تسک", callback_data=f"view_task|{task_id}")],
                    [InlineKeyboardButton(text="اتمام عملیات", callback_data=f"end_short_edit")],
                ]
            )

            # Update the message to show confirmation and options
            await callback_query.message.edit_text(success_message, reply_markup=view_keyboard)
            await callback_query.answer("✅ فایل‌ها با موفقیت اضافه شدند")

        # Handle invalid edit types
        else:
            await callback_query.answer("❌ دستور نامعتبر است")
            return

        # Check the result and notify the user
        if result == "NOT_EXIST":
            await callback_query.answer("❌ این تسک وجود ندارد")
            return
        elif result:
            # Confirm the edit and provide buttons to view task or finish
            view_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📋 دیدن تسک", callback_data=f"view_task|{task_id}")],
                    [InlineKeyboardButton(text="اتمام عملیات", callback_data=f"end_short_edit")],
                ]
            )
            await callback_query.message.edit_text(success_message, reply_markup=view_keyboard)
            await callback_query.answer("✅ تغییرات با موفقیت انجام شد")
        else:
            await callback_query.answer("❌ تغییرات ناموفق بود")

    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌ خطا در انجام عملیات", show_alert=True)
        except Exception:
            logger.exception("Failed to send error message")
    
    finally:
        # Ensure the DB session is closed
        if db:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close DB session")


# ===== Callback Handler for Ending Short Edit =====
@router.callback_query(F.data == "end_short_edit")
async def short_edit_confirm(callback_query: CallbackQuery):
    try:
        # Delete the message when user finishes short edit
        await callback_query.message.delete()
    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await callback_query.answer("❌ خطا در اتمام عملیات")
        except Exception:
            logger.exception("Failed to send error message")          
