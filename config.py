import os
import logging
from typing import Set, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Остальные ключи (опционально, для других функций)
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SHAZAM_API_KEY = os.getenv("SHAZAM_API_KEY")

# Проверка обязательных ключей
if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не найден!")
if not OPENROUTER_API_KEY:
    raise ValueError("❌ OPENROUTER_API_KEY не найден! Получите ключ на https://openrouter.ai")

OWNER_USER_ID = int(os.getenv("OWNER_USER_ID")) if os.getenv("OWNER_USER_ID") else None
AUTO_MODERATION_ENABLED = True

pending_requests = {}
OWNER_NAME = None
OWNER_DESCRIPTION = "парень с карими глазами, высокий, красивый, умный и обаятельный"

disabled_chats: Set[int] = set()
BAD_WORDS = []
MAX_MEMORY = 50

# Модели больше не нужны, т.к. используем OpenRouter
# Удаляем, чтобы не было путаницы
# MODELS = []

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)