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
from utils.date_utils import gregorian_to_jalali, jalali_to_gregorian, is_future_date, parse_flexible_date
from utils.texts import t

media_cache = {}
INLINE_COMMAND_ACTIONS = {
    "add_task": ("add", "افزودن تسک"),
    "assign_user": ("user", "تخصیص کاربر"),
    "title": ("title", "تغییر عنوان"),
    "desc": ("desc", "تغییر شرح"),
    "deadline": ("time", "تغییر ددلاین"),
    "attach": ("attach", "افزودن پیوست"),
}


def _inline_cmd_button(action_key: str) -> InlineKeyboardButton:
    """Build an inline button that pre-fills the related slash command in the chat input."""
    command, label = INLINE_COMMAND_ACTIONS[action_key]
    return InlineKeyboardButton(text=label, switch_inline_query_current_chat=f"/{command} ")


async def _is_user_in_chat(bot, chat_id: int, telegram_user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=telegram_user_id)
        return member is not None
    except Exception:
        return False


def _build_teledo_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    """
    Build the Teledo menu keyboard.
    - Admins see all actions.
    - Non-admins only see "تسک های من" and "افزودن تسک".
    """
    rows = []
    if is_admin:
        rows.append([InlineKeyboardButton(text="مدیریت کاربران", callback_data="teledo|users")])
        rows.append([InlineKeyboardButton(text="مدیریت تسک ها", callback_data="teledo|tasks")])

    # Shared entries
    rows.append([_inline_cmd_button("add_task")])

    if is_admin:
        rows.extend([
            [_inline_cmd_button("assign_user")],
            [_inline_cmd_button("title")],
            [_inline_cmd_button("desc")],
            [_inline_cmd_button("deadline")],
            [_inline_cmd_button("attach")],
        ])

    rows.append([InlineKeyboardButton(text="تسک های من", callback_data="teledo|my_tasks")])
    rows.append([InlineKeyboardButton(text="لغو", callback_data="teledo|cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
                if attachment_id.startswith("text:"):
                    content = attachment_id.removeprefix("text:").strip()
                    body = f"{text_msg}\n\n{content}" if content else text_msg
                    await callback_obj.bot.send_message(chat_id=user.telegram_id, text=body)
                elif attachment_id.startswith("AgAC"):
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


async def _notify_assigned_users(task, bot, text_key: str):
    """Notify all assigned users about a task update."""
    db = next(get_db())
    try:
        users = TaskService.get_task_users(db=db, task_id=task.id) or []
        for u in users:
            if not u.telegram_id:
                continue
            try:
                await bot.send_message(
                    chat_id=u.telegram_id,
                    text=t(text_key, title=task.title),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text=t("notify_task_assigned_btn"), callback_data=f"show_task|{task.id}")]]
                    )
                )
            except Exception:
                logger.exception("Failed to notify assigned user")
                continue
    finally:
        try:
            db.close()
        except Exception:
            logger.exception("Failed to close db in notify helper")


async def _notify_status_change(task, bot, actor_is_admin: bool, actor_username: str, new_status: str):
    """Notify admin or users about status change."""
    db = next(get_db())
    try:
        if actor_is_admin:
            recipients = TaskService.get_task_users(db=db, task_id=task.id) or []
            text_key = "notify_status_changed_users"
        else:
            admin = UserService.get_user(db=db, user_ID=task.admin_id)
            recipients = [admin] if admin else []
            text_key = "notify_status_changed_admin"

        for u in recipients:
            if not u or not u.telegram_id:
                continue
            try:
                await bot.send_message(
                    chat_id=u.telegram_id,
                    text=t(text_key, title=task.title, username=actor_username, status=new_status),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text=t("notify_task_assigned_btn"), callback_data=f"show_task|{task.id}")]]
                    )
                )
            except Exception:
                logger.exception("Failed to notify status change")
                continue
    finally:
        try:
            db.close()
        except Exception:
            logger.exception("Failed to close db in status notify helper")
@exception_decorator
def chunk_list(lst:list, chunk_size: int) -> List[List] | None:
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def task_manage_keyboard(
    db,
    group_id: int | None = None,
    topic_id: int | None = None,
    group_name: str | None = None,
    topic_name: str | None = None,
) -> Tuple[List[str], InlineKeyboardMarkup]:
    text = []
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    # If we are inside a specific topic, only show tasks from that topic.
    if topic_id is not None:
        tasks = TaskService.get_all_tasks(db=db, topic_id=topic_id) or []
        title = f"تسک های تاپیک {topic_name}" if topic_name else "تسک های این تاپیک"
        text.append(title)
        text.append(f"تعداد: {len(tasks)}")
        keyboard.inline_keyboard.extend(
            [
                [InlineKeyboardButton(text=task.title, callback_data=f"view_task|{task.id}") for task in chunk]
                for chunk in chunk_list(tasks, 2) or []
            ]
        )
        return text, keyboard

    # If we are scoped to a single group, list that group's topics/tasks only.
    if group_id is not None:
        topics = TaskService.get_all_topics(db=db, group_id=group_id) or []
        if topics:
            text.append(f"تاپیک های گروه {group_name}" if group_name else "تاپیک های گروه")
            text.append(f"تعداد: {len(topics)}")
            keyboard.inline_keyboard.extend(
                [
                    [InlineKeyboardButton(text=tp.name, callback_data=f"view_topic|{tp.id}") for tp in chunk]
                    for chunk in chunk_list(topics, 2) or []
                ]
            )
            keyboard.inline_keyboard.append(
                [
                    InlineKeyboardButton(text="باز گشت 🔙", callback_data="back"),
                    InlineKeyboardButton(text="سایر ...", callback_data=f"view_topic|OTHER|{group_id}"),
                ]
            )
        else:
            tasks = TaskService.get_all_tasks(db=db, group_id=group_id) or []
            text.append(f"تسک های گروه {group_name}" if group_name else "تسک های گروه")
            text.append(f"تعداد: {len(tasks)}")
            keyboard.inline_keyboard.extend(
                [
                    [InlineKeyboardButton(text=task.title, callback_data=f"view_task|{task.id}") for task in chunk]
                    for chunk in chunk_list(tasks, 2) or []
                ]
            )
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="باز گشت 🔙", callback_data="back")])
        return text, keyboard

    groups = TaskService.get_all_groups(db=db)
    if not groups:
        text.append("⚠️ هیچ گروهی برای نمایش وجود ندارد ⚠️")
        tasks = TaskService.get_all_tasks(db=db) or []
        if tasks:
            text.append(f"تسک ها : \n تعداد: {len(tasks)}")
            keyboard.inline_keyboard.extend(
                [
                    [InlineKeyboardButton(text=task.title, callback_data=f"view_task|{task.id}") for task in chunk]
                    for chunk in chunk_list(tasks, 2) or []
                ]
            )
        return text, keyboard

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
@router.message(Command("tasks_management"))
@router.message(F.text == "مدیریت تسک ها")
async def handle_task_manage(event: Message | CallbackQuery):
    """Main handler for manage tasks"""
    db = None
    try:
        db = next(get_db())  # Open a database session        
        # Check admin permission before proceeding
        permission = await admin_require(db, event)
        if not permission:
            return

        # Detect current chat/thread context to scope listings
        msg = event.message if isinstance(event, CallbackQuery) else event
        topic_ctx = None
        group_ctx = None
        topic_name = None
        group_name = None
        if msg.chat.type in ("group", "supergroup"):
            if getattr(msg, "is_topic_message", False):
                topic_ctx = TaskService.get_topic(db=db, tID=str(msg.message_thread_id))
                if topic_ctx:
                    topic_name = topic_ctx.name
                    group_ctx = TaskService.get_group(db=db, id=topic_ctx.group_id) if topic_ctx.group_id else None
                    group_name = group_ctx.name if group_ctx else None
            else:
                group_ctx = TaskService.get_group(db=db, tID=str(msg.chat.id))
                group_name = group_ctx.name if group_ctx else None

        # Build keyboard scoped to topic/group when applicable
        text, keyboard = task_manage_keyboard(
            db=db,
            group_id=group_ctx.id if group_ctx else None,
            topic_id=topic_ctx.id if topic_ctx else None,
            group_name=group_name,
            topic_name=topic_name,
        )

        text="\n".join(text)
        # Add cancel option to exit menu
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="لغو", callback_data="teledo|cancel")])
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text=text, reply_markup=keyboard)
        else:
            await event.answer(text=text, reply_markup=keyboard)
            await del_message(3, event)
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
        current_user = UserService.get_user(
            db=db,
            user_tID=str(callback_query.from_user.id),
            username=callback_query.from_user.username,
        )
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
                [InlineKeyboardButton(text=t("btn_set_group"), callback_data=f"edit_group|{task.id}"), InlineKeyboardButton(text=t("btn_set_topic"), callback_data=f"edit_topic|{task.id}")],
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

        group_line = f"گروه: {group.name}\n" if group else ""
        topic_line = f"تاپیک: {topic.name}\n" if topic else ""
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
        user = UserService.get_user(
            db=db,
            user_tID=str(callback_query.from_user.id),
            username=callback_query.from_user.username,
        )
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
        user = UserService.get_user(
            db=db,
            user_tID=str(callback_query.from_user.id),
            username=callback_query.from_user.username,
        )
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
            await _notify_status_change(
                task,
                callback_query.bot,
                actor_is_admin=is_admin,
                actor_username=callback_query.from_user.username or callback_query.from_user.full_name,
                new_status=new_status,
            )
        except Exception:
            logger.exception("Failed to send status change notifications")
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
    waiting_for_group_name = State()
    waiting_for_topic_name = State()


# ====== Edit Task Group ======
@router.callback_query(F.data.startswith("edit_group|"))
async def handle_edit_group(callback_query: CallbackQuery, state: FSMContext):
    """Show group selection for a task (existing groups or create new)."""
    db = None
    try:
        task_id = int(callback_query.data.split("|")[1])
        db = next(get_db())
        groups = TaskService.get_all_groups(db=db) or []

        keyboard_buttons = [
            [
                InlineKeyboardButton(
                    text=grp.name,
                    callback_data=f"select_group|{grp.id}|{task_id}"
                ) for grp in chunk
            ]
            for chunk in (chunk_list(groups, 2) or [])
        ]

        keyboard_buttons.append([
            InlineKeyboardButton(text=t("btn_group_other"), callback_data=f"select_group|NONE|{task_id}")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text=t("btn_create_group"), callback_data=f"create_group|{task_id}")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text=t("btn_back"), callback_data=f"view_task|{task_id}")
        ])

        await callback_query.message.edit_text(
            t("task_group_select_prompt"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        )
        await callback_query.answer()
    except Exception:
        logger.exception("Failed to show group selection")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error toast for group selection")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in handle_edit_group")


@router.callback_query(F.data.startswith("select_group|"))
async def handle_select_group(callback_query: CallbackQuery):
    """Assign selected group (or سایر) to the task."""
    db = None
    try:
        _, group_id_raw, task_id_raw = callback_query.data.split("|")
        task_id = int(task_id_raw)
        group_id_val = None if group_id_raw == "NONE" else int(group_id_raw)

        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        group = TaskService.get_group(db=db, id=group_id_val) if group_id_val else None
        res = TaskService.edit_task(db=db, task_id=task_id, group_id=group_id_val)
        if res == "NOT_EXIST":
            await callback_query.answer(t("task_not_found"), show_alert=True)
            return
        group_name = group.name if group else t("group_other_label")

        await callback_query.message.edit_text(
            t("task_group_set_success", group=group_name),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text=t("btn_back"), callback_data=f"view_task|{task_id}")
                ]]
            )
        )
        try:
            await _notify_assigned_users(task, callback_query.bot, "notify_task_updated_by_admin")
        except Exception:
            logger.exception("Failed to notify users about group change")
        await callback_query.answer()
    except Exception:
        logger.exception("Failed to set group for task")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error toast in set group")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in handle_select_group")


@router.callback_query(F.data.startswith("create_group|"))
async def handle_create_group_prompt(callback_query: CallbackQuery, state: FSMContext):
    """Prompt for a new group name then create and assign it."""
    try:
        task_id = int(callback_query.data.split("|")[1])
        await state.update_data(task_id=task_id, prompt_msg_id=callback_query.message.message_id)
        await state.set_state(EditTaskStates.waiting_for_group_name)

        await callback_query.message.edit_text(
            t("task_group_enter_name"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text=t("btn_back"), callback_data=f"edit_group|{task_id}")
                ]]
            )
        )
        await callback_query.answer()
    except Exception:
        logger.exception("Failed to prompt for new group name")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error toast in create_group prompt")


@router.message(EditTaskStates.waiting_for_group_name)
async def process_new_group_name(message: Message, state: FSMContext):
    """Create a new group and assign it to the task."""
    db = None
    try:
        name = message.text.strip()
        data = await state.get_data()
        task_id = int(data.get("task_id"))
        prompt_msg_id = data.get("prompt_msg_id")

        if not name:
            await message.answer(t("task_group_invalid_name"))
            return

        db = next(get_db())
        group = TaskService.create_group(db=db, name=name)
        if not group:
            await message.answer(t("task_create_group_failed"))
            return

        TaskService.edit_task(db=db, task_id=task_id, group_id=group.id)
        try:
            task = TaskService.get_task_by_id(db=db, id=task_id)
            await _notify_assigned_users(task, message.bot, "notify_task_updated_by_admin")
        except Exception:
            logger.exception("Failed to notify users about new group")

        try:
            await message.delete()
        except Exception:
            logger.exception("Could not delete group name message")

        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=t("task_group_create_success", group=name),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text=t("btn_back"), callback_data=f"view_task|{task_id}")
                    ]]
                )
            )
        except Exception:
            logger.exception("Failed to edit prompt message after group creation")
            await message.answer(
                t("task_group_create_success", group=name),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text=t("btn_back"), callback_data=f"view_task|{task_id}")
                    ]]
                )
            )

        await state.clear()
    except Exception:
        logger.exception("Unexpected error while creating new group")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message for group creation")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in process_new_group_name")


# ====== Edit Task Topic ======
@router.callback_query(F.data.startswith("edit_topic|"))
async def handle_edit_topic(callback_query: CallbackQuery, state: FSMContext):
    """Show topic selection within the task's group (or سایر)."""
    db = None
    try:
        task_id = int(callback_query.data.split("|")[1])
        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        if not task:
            await callback_query.answer(t("task_not_found"), show_alert=True)
            return
        if not task.group_id:
            await callback_query.answer(t("task_topic_requires_group"), show_alert=True)
            return

        topics = TaskService.get_all_topics(db=db, group_id=task.group_id) or []
        keyboard_buttons = [
            [
                InlineKeyboardButton(
                    text=tp.name,
                    callback_data=f"select_topic|{tp.id}|{task_id}"
                ) for tp in chunk
            ]
            for chunk in (chunk_list(topics, 2) or [])
        ]
        keyboard_buttons.append([
            InlineKeyboardButton(text=t("btn_topic_other"), callback_data=f"select_topic|NONE|{task_id}")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text=t("btn_create_topic"), callback_data=f"create_topic|{task_id}")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text=t("btn_back"), callback_data=f"view_task|{task_id}")
        ])

        await callback_query.message.edit_text(
            t("task_topic_select_prompt"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        )
        await callback_query.answer()
    except Exception:
        logger.exception("Failed to show topic selection")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error toast for topic selection")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in handle_edit_topic")


@router.callback_query(F.data.startswith("select_topic|"))
async def handle_select_topic(callback_query: CallbackQuery):
    """Assign selected topic (or سایر) to the task."""
    db = None
    try:
        _, topic_id_raw, task_id_raw = callback_query.data.split("|")
        task_id = int(task_id_raw)
        topic_id_val = None if topic_id_raw == "NONE" else int(topic_id_raw)

        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        topic = TaskService.get_topic(db=db, id=topic_id_val) if topic_id_val else None
        res = TaskService.edit_task(db=db, task_id=task_id, topic_id=topic_id_val)
        if res == "NOT_EXIST":
            await callback_query.answer(t("task_not_found"), show_alert=True)
            return
        topic_name = topic.name if topic else t("topic_other_label")

        await callback_query.message.edit_text(
            t("task_topic_set_success", topic=topic_name),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text=t("btn_back"), callback_data=f"view_task|{task_id}")
                ]]
            )
        )
        try:
            await _notify_assigned_users(task, callback_query.bot, "notify_task_updated_by_admin")
        except Exception:
            logger.exception("Failed to notify users about topic change")
        await callback_query.answer()
    except Exception:
        logger.exception("Failed to set topic for task")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error toast in set topic")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in handle_select_topic")


@router.callback_query(F.data.startswith("create_topic|"))
async def handle_create_topic_prompt(callback_query: CallbackQuery, state: FSMContext):
    """Prompt for a new topic name then create and assign it to the task's group."""
    db = None
    try:
        task_id = int(callback_query.data.split("|")[1])
        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        if not task or not task.group_id:
            await callback_query.answer(t("task_topic_requires_group"), show_alert=True)
            return

        await state.update_data(task_id=task_id, prompt_msg_id=callback_query.message.message_id)
        await state.set_state(EditTaskStates.waiting_for_topic_name)

        await callback_query.message.edit_text(
            t("task_topic_enter_name"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text=t("btn_back"), callback_data=f"edit_topic|{task_id}")
                ]]
            )
        )
        await callback_query.answer()
    except Exception:
        logger.exception("Failed to prompt for new topic name")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error toast in create_topic prompt")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in handle_create_topic_prompt")


@router.message(EditTaskStates.waiting_for_topic_name)
async def process_new_topic_name(message: Message, state: FSMContext):
    """Create a new topic in the task's group and assign it."""
    db = None
    try:
        name = message.text.strip()
        data = await state.get_data()
        task_id = int(data.get("task_id"))
        prompt_msg_id = data.get("prompt_msg_id")

        if not name:
            await message.answer(t("task_topic_invalid_name"))
            return

        db = next(get_db())
        task = TaskService.get_task_by_id(db=db, id=task_id)
        if not task or not task.group_id:
            await message.answer(t("task_topic_requires_group"))
            await state.clear()
            return

        topic = TaskService.create_topic(db=db, group_id=task.group_id, name=name)
        if not topic:
            await message.answer(t("task_create_topic_failed"))
            return

        TaskService.edit_task(db=db, task_id=task_id, topic_id=topic.id)
        try:
            await _notify_assigned_users(task, message.bot, "notify_task_updated_by_admin")
        except Exception:
            logger.exception("Failed to notify users about new topic")

        try:
            await message.delete()
        except Exception:
            logger.exception("Could not delete topic name message")

        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                text=t("task_topic_create_success", topic=name),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text=t("btn_back"), callback_data=f"view_task|{task_id}")
                    ]]
                )
            )
        except Exception:
            logger.exception("Failed to edit prompt message after topic creation")
            await message.answer(
                t("task_topic_create_success", topic=name),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text=t("btn_back"), callback_data=f"view_task|{task_id}")
                    ]]
                )
            )

        await state.clear()
    except Exception:
        logger.exception("Unexpected error while creating new topic")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message for topic creation")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in process_new_topic_name")


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

        if res:
            try:
                task = TaskService.get_task_by_id(db=db, id=task_id)
                await _notify_assigned_users(task, message.bot, "notify_task_updated_by_admin")
            except Exception:
                logger.exception("Failed to notify users about name change")

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

        if res:
            try:
                task = TaskService.get_task_by_id(db=db, id=task_id)
                await _notify_assigned_users(task, message.bot, "notify_task_updated_by_admin")
            except Exception:
                logger.exception("Failed to notify users about description change")

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

        # Try to parse Jalali date (YYYY-MM-DD) and ensure it is in the future
        new_end = jalali_to_gregorian(date_text)
        error_key = None
        if not new_end:
            error_key = "deadline_invalid_format"
        elif not is_future_date(new_end):
            error_key = "deadline_past_date"

        if error_key:
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
                err_msg = await message.answer(t(error_key))
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
            try:
                task = TaskService.get_task_by_id(db=db, id=task_id)
                await _notify_assigned_users(task, message.bot, "notify_task_updated_by_admin")
            except Exception:
                logger.exception("Failed to notify users about deadline change")
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
                    text=t("notify_task_assigned", title=task.title),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text=t("notify_task_assigned_btn"), callback_data=f"show_task|{task_id}")]
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
        user = UserService.get_user(
            db=db,
            user_tID=str(callback_query.from_user.id),
            username=callback_query.from_user.username,
        )
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
            adder_user = UserService.get_user(
                db=db,
                user_tID=str(message.from_user.id),
                username=message.from_user.username,
            )
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
        user = UserService.get_user(
            db=db,
            user_tID=str(callback_query.from_user.id),
            username=callback_query.from_user.username,
        )
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
            if file_id.startswith("text:"):
                content = file_id.removeprefix("text:").strip() or "پیوست متنی"
                await callback_query.message.bot.send_message(
                    chat_id=callback_query.message.chat.id,
                    text=content
                )
            elif file_id.startswith("AgAC"):  # likely photo/video (depends on your storage)
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
        keyboard_buttons.append([InlineKeyboardButton(text="لغو", callback_data="teledo|cancel")])
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
@router.message(Command("my_tasks"))
async def handle_my_tasks_message(message: Message):
    await handle_my_tasks(event=message)
    await del_message(3, message)

@router.callback_query(F.data == "back_show")
async def handle_my_tasks_callback(callback: CallbackQuery):
    await handle_my_tasks(event=callback)


@router.message(Command("teledo"))
async def handle_teledo_menu(message: Message):
    """Show Teledo menu for admins in groups, supergroups, or private chats."""
    db = None
    try:
        db = next(get_db())

        # Determine admin status based on chat type
        if message.chat.type in ("group", "supergroup"):
            chat_member = await message.bot.get_chat_member(
                chat_id=message.chat.id,
                user_id=message.from_user.id,
            )
            is_admin = chat_member.status in ["administrator", "creator"]
        elif message.chat.type == "private":
            is_admin = bool(
                UserService.is_admin(
                    db=db, user_tID=str(message.from_user.id), username=message.from_user.username
                )
            )
        else:
            await message.answer(t("only_group_command"))
            return

        if not is_admin:
            em = await message.answer(t("teledo_admin_only"))
            await del_message(3, em, message)
            return

        keyboard = _build_teledo_keyboard(is_admin=is_admin)
        await message.answer("یک گزینه را انتخاب کنید:", reply_markup=keyboard)
        await del_message(3, message)
    except Exception:
        logger.exception("Failed to show teledo menu")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send teledo menu fallback")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in teledo menu")


@router.callback_query(F.data.startswith("teledo|"))
async def handle_teledo_callbacks(callback_query: CallbackQuery):
    """Handle Teledo menu callbacks."""
    action = callback_query.data.split("|", 1)[1] if "|" in callback_query.data else ""
    db = None
    try:
        db = next(get_db())

        chat_type = callback_query.message.chat.type
        if chat_type in ("group", "supergroup"):
            chat_member = await callback_query.bot.get_chat_member(
                chat_id=callback_query.message.chat.id,
                user_id=callback_query.from_user.id,
            )
            is_admin = chat_member.status in ["administrator", "creator"]
        elif chat_type == "private":
            is_admin = bool(
                UserService.is_admin(
                    db=db, user_tID=str(callback_query.from_user.id), username=callback_query.from_user.username
                )
            )
        else:
            await callback_query.answer(t("invalid_command"))
            return

        if not is_admin:
            await callback_query.answer(t("teledo_admin_only"), show_alert=True)
            return

        # Cancel: remove menu message
        if action == "cancel":
            try:
                await callback_query.message.delete()
            except Exception:
                pass
            await callback_query.answer()
            return

        if action == "users":
            from handlers.user_handlers.add import view_users  # local import to avoid circular
            await view_users(callback_query=callback_query)
            return

        if action == "tasks":
            await handle_task_manage(callback_query)
            return

        if action == "my_tasks":
            await handle_my_tasks(event=callback_query)
            return

        admin_only_actions = {"add_task", "assign_user", "title", "desc", "deadline", "attach", "tasks", "users"}
        if action in admin_only_actions and not is_admin:
            await callback_query.answer(t("no_permission_cmd"), show_alert=True)
            return

        if action in INLINE_COMMAND_ACTIONS:
            command, label = INLINE_COMMAND_ACTIONS[action]
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"/{command}", switch_inline_query_current_chat=f"/{command} ")],
                    [InlineKeyboardButton(text=t("btn_cancel"), callback_data="teledo|cancel")],
                ]
            )
            helper_text = f"{label}\nدستور در نوار نوشتار قرار گرفت، متن یا فایل را بعد از آن اضافه و ارسال کنید."
            await callback_query.message.edit_text(helper_text, reply_markup=keyboard)
            await callback_query.answer()
            return

        await callback_query.answer(t("invalid_command"))
    except Exception:
        logger.exception("Failed to handle teledo callback")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send teledo callback fallback")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db in teledo callback")
# ===== Command Picker (prefills input instead of sending) =====
@router.message(Command("commands"))
@router.message(Command("menu"))
@router.message(F.text.in_({"/commands", "commands", "/menu", "menu"}))
async def handle_command_picker(message: Message):
    try:
        # Allow in both group and supergroup. In private, fall back to admin-only set.
        is_group = message.chat.type in ("group", "supergroup")

        if not message.reply_to_message:
            em = await message.answer(t("commands_reply_required"))
            await del_message(3, em, message)
            return

        chat_member = None
        is_admin = False
        if is_group:
            chat_member = await message.bot.get_chat_member(
                chat_id=message.chat.id,
                user_id=message.from_user.id
            )
            is_admin = chat_member.status in ["administrator", "creator"]
        else:
            # Private: trust DB check if available
            db = next(get_db())
            is_admin = bool(UserService.is_admin(db=db, user_tID=str(message.from_user.id), username=message.from_user.username))
            db.close()

        buttons = []
        if is_admin:
            buttons.extend([
                [InlineKeyboardButton(text="➕ /add", switch_inline_query_current_chat="/add ")],
                [InlineKeyboardButton(text="👤 /user", switch_inline_query_current_chat="/user ")],
                [InlineKeyboardButton(text="🏷️ /title", switch_inline_query_current_chat="/title ")],
                [InlineKeyboardButton(text="📝 /desc", switch_inline_query_current_chat="/desc ")],
                [InlineKeyboardButton(text="⏳ /time", switch_inline_query_current_chat="/time ")],
                [InlineKeyboardButton(text="📎 /attach", switch_inline_query_current_chat="/attach ")],
            ])
        else:
            buttons.append(
                [InlineKeyboardButton(text="📎 /attach", switch_inline_query_current_chat="/attach ")]
            )

        cmd_help_lines = [
            "/add : افزودن تسک جدید بر اساس متن پیام ریپلای به عنوان عنوان.",
            "/user : کاربرِ پیام ریپلای را برای افزودن به یک تسک موجود انتخاب می‌کند.",
            "/title : متن ریپلای را به عنوان عنوان جدید یک تسک موجود ثبت می‌کند.",
            "/desc : متن ریپلای را توضیح تسک می‌کند؛ اگر توضیح نداشت اضافه می‌شود و اگر داشت جایگزین می‌شود.",
            "/time : زمان در پیام ریپلای (با تشخیص فرمت تاریخ) را برای یک تسک تنظیم می‌کند؛ در صورت نامعتبر بودن فرمت صحیح را نمایش می‌دهد.",
            "/attach : پیام ریپلای (متن یا فایل) را به یک تسک موجود پیوست می‌کند و پیوست‌های قبلی را حذف نمی‌کند.",
            "تمام این دستورات فقط روی پیام ریپلای کار می‌کنند.",
        ]

        reply = await message.answer(
            "\n".join(cmd_help_lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        await del_message(3, reply, message)
    except Exception:
        logger.exception("Failed to send command picker")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send fallback error in command picker")


# ===== Short Edit Commands Handler (Time, Attach, Desc and Title commands) =====
@router.message(Command("title"))
@router.message(Command("name"))
@router.message(Command("desc"))
@router.message(Command("des"))
@router.message(Command("attach"))
@router.message(Command("time"))
async def handle_short_edits(message: Message):
    await _handle_short_edits(message, allow_without_reply=False)

async def _handle_short_edits(message: Message, allow_without_reply: bool = False):
    db = None
    try:
        db = next(get_db())
        current_user = UserService.get_user(
            db=db,
            user_tID=str(message.from_user.id),
            username=message.from_user.username,
        )
        is_admin = UserService.is_admin(
            db=db,
            user_tID=message.from_user.id,
            username=message.from_user.username,
        )

        # These commands must be used as a reply to another message unless explicitly allowed
        if not allow_without_reply and not message.reply_to_message:
            em = await message.answer(t("commands_reply_required"))
            await del_message(3, em, message)
            return

        # Restrict non-admins to /attach only
        if message.text.lower().split()[0] != "/attach" and not is_admin:
            await message.delete()
            em = await message.answer(t("no_permission_cmd"))
            await del_message(3, em)
            return

        # Figure out which command was used and grab the payload from either:
        # 1) text after the command, or 2) the replied message.
        cmd_token = message.text.split()[0].lower().lstrip("/")
        command_used = cmd_token.split("@")[0]
        value_text = None

        parts = message.text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            value_text = parts[1].strip()
        elif message.reply_to_message:
            reply_text = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
            value_text = reply_text if reply_text else None

        callback_text = None

        if command_used in ("title", "name"):
            if not value_text:
                em = await message.answer(t("invalid_command"))
                await del_message(3, em, message)
                return
            callback_text = f"short_edit|name|{value_text}"

        elif command_used in ("desc", "des"):
            if not value_text:
                em = await message.answer(t("invalid_command"))
                await del_message(3, em, message)
                return
            callback_text = f"short_edit|des|{value_text}"

        elif command_used == "time":
            if not value_text:
                em = await message.answer(t("deadline_invalid_format"))
                await del_message(3, em, message)
                return
            parsed = parse_flexible_date(value_text)
            if not parsed:
                em = await message.answer(t("deadline_invalid_format"))
                await del_message(3, em, message)
                return
            if not is_future_date(parsed):
                em = await message.answer(t("deadline_past_date"))
                await del_message(3, em, message)
                return
            callback_text = f"short_edit|time|{value_text}"

        elif command_used in ("attach", "atach"):
            file_ids = []
            seen_ids = set()

            def collect_media(msg):
                if not msg:
                    return
                possible_ids = []
                if getattr(msg, "photo", None):
                    possible_ids.append(msg.photo[-1].file_id)
                if getattr(msg, "video", None):
                    possible_ids.append(msg.video.file_id)
                if getattr(msg, "audio", None):
                    possible_ids.append(msg.audio.file_id)
                if getattr(msg, "voice", None):
                    possible_ids.append(msg.voice.file_id)
                if getattr(msg, "document", None):
                    possible_ids.append(msg.document.file_id)
                for fid in possible_ids:
                    if fid and fid not in seen_ids:
                        seen_ids.add(fid)
                        file_ids.append(fid)

            collect_media(message.reply_to_message)
            collect_media(message)

            if file_ids:
                media_key = str(uuid.uuid4())
                media_cache[media_key] = file_ids
                callback_text = f"short_edit|attach|{media_key}"
            elif value_text:
                media_key = str(uuid.uuid4())
                media_cache[media_key] = [f"text:{value_text}"]
                callback_text = f"short_edit|attach|{media_key}"
            else:
                em = await message.answer("لطفاً یک متن یا فایل را بعد از /attach ارسال کنید.")
                await del_message(3, em, message)
                return

        # If no command matched or we didn't get a value, notify the user
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
                group = TaskService.get_group(db=db, id=topic.group_id) if topic and topic.group_id else None
            else:
                group = TaskService.get_group(db=db, tID=str(message.chat.id))
                if not group:
                    em = await message.answer(t("no_tasks_group"))
                    await del_message(3, message, em)
                    return
                tasks = TaskService.get_all_tasks(db=db, group_id=group.id, topic_id=False)
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
        for task_item in tasks:
            keyboard.append([
                InlineKeyboardButton(text=task_item.title, callback_data=f"{callback_text}|{task_item.id}")
            ])
        keyboard.append([InlineKeyboardButton(text=t("btn_cancel"), callback_data="teledo|cancel")])

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
    await _handle_short_users_edits(message, allow_fallback_list=True)


async def _handle_short_users_edits(message: Message, allow_fallback_list: bool = False):
    """A handler to add a user to a task"""
    db = None
    try:
        db = next(get_db())  # Open a database session

        # Check admin permission before proceeding
        permission = await admin_require(db=db, message=message)
        if not permission:
            return

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
                tasks = TaskService.get_all_tasks(db=db, group_id=group.id, topic_id=False)
        else:
            em = await message.answer(t("only_group_command"))
            await del_message(3, em)
            return

        # Determine target user either from reply or from the argument
        parts = (message.text or "").split(maxsplit=1)
        arg_username = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        target_user = None
        reply_user = message.reply_to_message.from_user if message.reply_to_message else None

        group_users = [u for u in UserService.get_all_users(db=db) if u.telegram_id] if message.chat.type in ("group", "supergroup") else []

        if arg_username:
            cleaned_username = arg_username.lstrip("@")
            if not cleaned_username:
                em = await message.answer(t("invalid_command"))
                await del_message(3, em, message)
                return
            candidate = next((u for u in group_users if u.username and u.username.lower() == cleaned_username.lower()), None)
            if candidate and candidate.telegram_id and await _is_user_in_chat(message.bot, message.chat.id, int(candidate.telegram_id)):
                target_user = candidate
        elif reply_user and not reply_user.is_bot:
            target_user = UserService.get_or_create_user(
                db=db,
                username=reply_user.username or f"user_{reply_user.id}",
                telegram_id=reply_user.id,
                is_admin=False,
            )
            if target_user and target_user.telegram_id:
                in_chat = await _is_user_in_chat(message.bot, message.chat.id, int(target_user.telegram_id))
                if not in_chat:
                    target_user = None

        await message.delete()

        # If no tasks found, notify user
        if not tasks:
            em = await message.answer(t("no_tasks_found"))
            await del_message(3, em, message)
            return

        # If a valid target_user (existing in group or created via reply) is set, go straight to task selection for that user.
        # Otherwise, list available users in group/topic to pick from.
        if target_user:
            keyboard = []
            for tsk in tasks:
                keyboard.append([
                    InlineKeyboardButton(text=tsk.title, callback_data=f"assign_user_direct|{target_user.id}|{tsk.id}")
                ])
            keyboard.append([InlineKeyboardButton(text=t("btn_cancel"), callback_data="teledo|cancel")])

            await message.answer(
                t("select_task_prompt"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
            )
            return

        if not allow_fallback_list:
            em = await message.answer(t("user_not_found"))
            await del_message(3, em, message)
            return

        if not group_users:
            em = await message.answer(t("user_none_found"))
            await del_message(3, em, message)
            return

        keyboard_users = []
        for u in group_users:
            label = u.username or f"user_{u.id}"
            keyboard_users.append([
                InlineKeyboardButton(text=label, callback_data=f"assign_user_pick|{u.id}|{group.id}|{message.message_thread_id if message.is_topic_message else 'NONE'}")
            ])
        keyboard_users.append([InlineKeyboardButton(text=t("btn_cancel"), callback_data="teledo|cancel")])

        await message.answer(
            t("user_manage_title", user_count=len(group_users)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_users)
        )


    except Exception:
        # Log unexpected errors
        logger.exception("Unexpected error occurred")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send error message")          


# ===== Mention-prefixed commands (/add, /user, /title, /desc, /time, /attach) =====
@router.message(F.text.startswith("@"))
async def handle_mention_prefixed_commands(message: Message):
    """
    Support commands sent as "@bot /cmd ..." by stripping the mention and reusing existing handlers.
    """
    try:
        text = (message.text or "").strip()
        bot_username = config.BOT_USERNAME.lstrip("@")
        if not text.lower().startswith(f"@{bot_username.lower()}"):
            return

        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return

        rest = parts[1].strip()
        if not rest.startswith("/"):
            return

        # Clone message with normalized text (pydantic model is frozen)
        normalized_message: Message = message.model_copy(update={"text": rest})
        cmd_token = rest.split()[0].lower().lstrip("/")
        command_used = cmd_token.split("@")[0]

        if command_used == "add":
            from .add import add_task
            await add_task(normalized_message)
        elif command_used == "user":
            await handle_short_users_edits(normalized_message)
        elif command_used in ("title", "name", "desc", "des", "time", "attach", "atach"):
            await _handle_short_edits(normalized_message, allow_without_reply=True)
    except Exception:
        logger.exception("Failed to process mention-prefixed command")
        try:
            await message.answer(t("generic_error"))
        except Exception:
            logger.exception("Failed to send fallback for mention-prefixed command")


# ===== Callback Handler for Assigning User Directly =====
@router.callback_query(F.data.startswith("assign_user_direct|"))
async def handle_assign_user_direct(callback_query: CallbackQuery):
    db = None
    try:
        db = next(get_db())
        permission = await admin_require(db=db, message=callback_query)
        if not permission:
            return

        try:
            _, user_id_str, task_id_str = callback_query.data.split("|")
            user_id = int(user_id_str)
            task_id = int(task_id_str)
        except Exception:
            await callback_query.answer(t("generic_error"))
            return

        target_user = UserService.get_user(db=db, user_ID=user_id)
        task = TaskService.get_task_by_id(db=db, id=task_id)
        if not target_user or not task:
            await callback_query.answer(t("no_tasks_found"))
            return

        res = UserService.assign_user_to_task(db=db, user_ID=user_id, task_id=task_id)
        if not res:
            await callback_query.answer(t("generic_error"))
            return

        confirm_text = f"{target_user.username or 'User'} به تسک «{task.title}» اضافه شد."
        try:
            await callback_query.message.edit_text(
                confirm_text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=t("btn_view_assigned"), callback_data=f"view_task_users|{task.id}")],
                        [InlineKeyboardButton(text=t("btn_back"), callback_data="teledo|cancel")],
                    ]
                ),
            )
        except Exception:
            # If edit fails (e.g., message not modifiable), fall back to a reply
            await callback_query.message.answer(confirm_text)

        await callback_query.answer(confirm_text, show_alert=False)
    except Exception:
        logger.exception("Failed to assign user via quick command")
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


@router.callback_query(F.data.startswith("assign_user_pick|"))
async def handle_assign_user_pick(callback_query: CallbackQuery):
    """
    When a username argument wasn't resolved, we list users; picking one leads to task selection.
    Callback data: assign_user_pick|<user_id>|<group_id>|<topic_thread_id or 'NONE'>
    """
    db = None
    try:
        db = next(get_db())
        permission = await admin_require(db=db, message=callback_query)
        if not permission:
            return

        try:
            _, user_id_str, group_id_str, topic_thread = callback_query.data.split("|")
            user_id = int(user_id_str)
            group_id = int(group_id_str)
            topic_thread_id = None if topic_thread == "NONE" else topic_thread
        except Exception:
            await callback_query.answer(t("generic_error"))
            return

        target_user = UserService.get_user(db=db, user_ID=user_id)
        if not target_user:
            await callback_query.answer(t("user_not_found"))
            return

        # Fetch tasks depending on group/topic
        if topic_thread_id:
            topic = TaskService.get_topic(db=db, tID=str(topic_thread_id))
            tasks = TaskService.get_all_tasks(db=db, topic_id=topic.id) if topic else None
        else:
            group = TaskService.get_group(db=db, id=group_id)
            tasks = TaskService.get_all_tasks(db=db, group_id=group.id, topic_id=False) if group else None

        if not tasks:
            await callback_query.answer(t("no_tasks_found"), show_alert=True)
            return

        keyboard = []
        for tsk in tasks:
            keyboard.append([
                InlineKeyboardButton(text=tsk.title, callback_data=f"assign_user_direct|{target_user.id}|{tsk.id}")
            ])
        keyboard.append([InlineKeyboardButton(text=t("btn_cancel"), callback_data="teledo|cancel")])

        await callback_query.message.edit_text(
            t("select_task_prompt"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
        await callback_query.answer()
    except Exception:
        logger.exception("Failed to handle assign_user_pick")
        try:
            await callback_query.answer(t("generic_error"), show_alert=True)
        except Exception:
            logger.exception("Failed to send error message in assign_user_pick")
    finally:
        if db:
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
            end_date = parse_flexible_date(edit_value)
            if not end_date:
                await callback_query.answer(t("deadline_invalid_format"))
                return
            if not is_future_date(end_date):
                await callback_query.answer(t("deadline_past_date"))
                return
            result = TaskService.edit_task(db=db, task_id=task_id, end_date=end_date)
            success_message = f"??? ????? ????? ??? ?? {edit_value} ????? ???"
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
                    logger.exception(f"Failed to attach item {file_id} to task {task_id}")

            # Notify user if no files were added
            if added_count == 0:
                await callback_query.answer("❌ هیچ پیوستی اضافه نشد")
                return

            # Inform user about successful attachments and remove cache
            success_message = f"✅ تعداد {added_count} پیوست به تسک اضافه شد"
            file_ids = media_cache.__delitem__(media_key)

            # Send notifications with only new files
            task = TaskService.get_task_by_id(db=db, id=task_id)
            adder_user = UserService.get_user(
                db=db,
                user_tID=str(callback_query.from_user.id),
                username=callback_query.from_user.username,
            )
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
            await callback_query.answer("✅ پیوست‌ها با موفقیت اضافه شدند")

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
