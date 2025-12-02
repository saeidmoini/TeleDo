from __future__ import annotations
from typing import Literal
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/task_bot")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "") + "/webhook"
    WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
    WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", 8000))
    PROXY_URL = os.getenv("PROXY_URL", None)
    MODE: Literal["DEV", "PROD"] = os.getenv("MODE", "PROD")
    BOT_USERNAME = os.getenv("BOT_USERNAME", "my_bot")
    INITIAL_ADMIN_ID = os.getenv("INITIAL_ADMIN_ID")
    INITIAL_ADMIN_USERNAME = os.getenv("INITIAL_ADMIN_USERNAME")

config = Config()
