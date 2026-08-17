import os
import logging
from typing import Set, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SHAZAM_API_KEY = os.getenv("SHAZAM_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не найден!")
if not CEREBRAS_API_KEY:
    raise ValueError("❌ CEREBRAS_API_KEY не найден!")

OWNER_USER_ID = int(os.getenv("OWNER_USER_ID")) if os.getenv("OWNER_USER_ID") else None
AUTO_MODERATION_ENABLED = True

pending_requests = {}
OWNER_NAME = None
OWNER_DESCRIPTION = "парень с карими глазами, высокий, красивый, умный и обаятельный"

disabled_chats: Set[int] = set()
BAD_WORDS = []
MAX_MEMORY = 50
MODELS = ["gpt-oss-120b", "zai-glm-4.7"]

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)