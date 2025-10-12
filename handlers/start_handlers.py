from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ChatType
from services.task_services import TaskService
from services.user_services import UserService
from database import get_db
from logger import logger
from . import main_router as router
from . import chat_type_filter, get_main_menu_keyboard
from config import config
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram import F

class TopicStates(StatesGroup):
    waiting_for_name = State()

# ===== Start in Private chat =====
@router.message(Command("start"), chat_type_filter(ChatType.PRIVATE))
async def cmd_start_private(message: Message):
    """Handle /start command in private chats"""
    try:
        db = next(get_db())

        if message.from_user.is_bot:
            await message.answer("❌ ربات ها نمیتوانند از این ربات استفاده کنند ❌")
        
        # Get or create user based on mode
        if config.MODE == "DEV":
            # Development mode - create user if not exists with admin privileges
            user = UserService.get_or_create_user(
                db=db,
                telegram_id=str(message.from_user.id),
                username=message.from_user.username,
                is_admin=True,
            )
            if not user:
                await message.answer("❌ خطا در ایجاد کاربر")
                return
        else:
            # Production mode - only allow existing users
            user = UserService.get_user(
                db=db,
                user_tID=str(message.from_user.id),
                username=message.from_user.username,
            )

            if not user:
                await message.answer(
                    "❌ حساب کاربری شما پیدا نشد !\n\n"
                    "احتمالا حساب شما مجوز استفاده از این سرویس رو ندارد"
                )
                return
        
        # Create keyboard for main menu
        try:
            keyboard = get_main_menu_keyboard(chat_type=ChatType.PRIVATE, is_admin=user.is_admin)
        except Exception:
            logger.exception("error occurred in creating keyboard buttons")
            await message.answer("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            return

        # Send welcome message with keyboard
        await message.answer(
            f"سلام {message.from_user.first_name}! به ربات مدیریت تسک خوش آمدید.\n"
            "شما به عنوان ادمین ثبت شدید.",
            reply_markup=keyboard
        )       
    
    except Exception:
        logger.exception("Unexpected error occurred")
        try:
            await message.answer("❌خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.exception("Failed to send error message")   
    
    finally:
        # Ensure database connection is closed
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

# ===== Start in Group or Supergroup chat =====
@router.message(Command("start"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_start_group(message: Message, state: FSMContext):
    """Handle /start command in groups and supergroups"""
    db = None
    try:
        db = next(get_db())
        
        # Check if the user who triggered the command is an admin or the owner of the group
        chat_member = await message.bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=message.from_user.id
        )
        is_admin = chat_member.status in ["administrator", "creator"]
        if not is_admin:
            await message.answer("❌ فقط ادمین‌ها یا مالک گروه می‌توانند ربات را راه اندازی کنند.")
            return

        # In DEV mode: create or get the user and automatically mark as admin
        # In PROD mode: only existing users with admin rights are allowed
        if config.MODE == "DEV":
            user = UserService.get_or_create_user(
                db=db,
                telegram_id=str(message.from_user.id),
                username=message.from_user.username,
                is_admin=True
            )
        else:
            user = UserService.get_user(
                db=db,
                user_tID=str(message.from_user.id),
                username=message.from_user.username,
            )
            if not user or not user.is_admin:
                await message.answer("❌ شما دسترسی به اجرای این عملیات ندارید")
                return

        # Create or fetch the group record in the database
        group = TaskService.get_or_create_group(
            db, telegram_group_id=str(message.chat.id), name=message.chat.title
        )
        if not group:
            logger.exception("error occurred in creating group")
            await message.answer("❌ خطایی در پیدا کردن گروه رخ داد. لطفاً دوباره تلاش کنید.")
            return

        # If the message was sent inside a topic (thread) within a supergroup
        if message.chat.type == "supergroup" and message.is_topic_message and (topicID := message.message_thread_id):
            topic = TaskService.get_topic(db=db, tID=str(topicID))
            if topic:
                await message.answer(
                    f"✅ ربات با موفقیت راه اندازی شد!\n"
                )
                return
            # Save temporary topic-related data into FSM context
            await state.update_data(group_id=group.id, topic_id=topicID)
            
            # Build a shareable link to the topic
            chat_id = str(message.chat.id)
            thread_id = topicID
            topic_link = f"https://t.me/c/{chat_id[4:]}/{thread_id}"
            await state.update_data(topic_link=topic_link)

            # Ask the admin to provide a custom name for this topic
            await message.answer(
                f"📌 پیام از یک تاپیک دریافت شد.\n"
                f"🔗 لینک تاپیک: {topic_link}\n\n"
                f"لطفاً یک نام برای این تاپیک وارد کنید:"
            )
            # Move conversation into a waiting state to capture the topic name
            await state.set_state(TopicStates.waiting_for_name)
            return  # stop further execution until user provides the name

        # If the message is not inside a topic, just complete the group setup
        await message.answer(
            f"✅ ربات با موفقیت راه اندازی شد!\n"
            f"گروه {message.chat.title} ثبت شد.\n"
            f"شما به عنوان ادمین ثبت شدید."
        )

    except Exception:
        # Catch and log any unexpected error during setup
        logger.exception("Unexpected error occurred")
        try:
            await message.answer("❌ خطایی در راه اندازی ربات رخ داد.")
        except Exception:
            logger.exception("Failed to send error message")
    
    finally:
        # Always close database connection safely
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

# ----- Step 2: Handle the conversation where the bot waits for the topic name -----
@router.message(TopicStates.waiting_for_name)
async def process_topic_name(message: Message, state: FSMContext):
    db = None
    try:
        db = next(get_db())
        # Retrieve previously stored data from FSM context
        data = await state.get_data()
        group_id = data["group_id"]
        topic_id = data["topic_id"]
        topic_link = data["topic_link"]

        # Use the text sent by the admin as the topic name
        topic_name = message.text.strip()

        # Create or fetch the topic record in the database
        topic = TaskService.get_or_create_topic(
            db,
            telegram_topic_id=topic_id,
            group_id=group_id,
            name=topic_name,
            link=topic_link
        )
        if not topic:
            await message.answer("❌ خطا در ذخیره‌سازی تاپیک رخ داد.")
            return

        # Prepare and return the main group keyboard
        keyboard = get_main_menu_keyboard(chat_type=ChatType.GROUP)

        # Send confirmation to the admin with topic details
        await message.answer(
            f"✅ تاپیک با موفقیت ثبت شد!\n\n"
            f"📌 نام: {topic_name}\n"
            f"🔗 لینک: {topic_link}",
            reply_markup=keyboard
        )

    except Exception:
        # Catch and log any error during topic saving
        logger.exception("Failed to save topic")
        await message.answer("❌ خطا در ذخیره‌سازی تاپیک رخ داد.")
    
    finally:
        # Always close the database connection and reset FSM state
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close db")

        await state.clear()
