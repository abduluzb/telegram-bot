# api_clients.py
from cerebras.cloud.sdk import Cerebras
from config import (
    CEREBRAS_API_KEY,
    YOUTUBE_API_KEY,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    logger
)
from googleapiclient.discovery import build

# === Cerebras клиент ===
ai_clients = []

if CEREBRAS_API_KEY:
    cerebras_client = Cerebras(api_key=CEREBRAS_API_KEY)

    # Основная модель
    ai_clients.append({
        "name": "Cerebras (qwen-3-27b)",
        "client": cerebras_client,
        "model": "qwen-3-27b",
        "max_tokens": 500,
        "temperature": 0.8,
    })

    # Резервная модель
    ai_clients.append({
        "name": "Cerebras (llama3.1-8b)",
        "client": cerebras_client,
        "model": "llama3.1-8b",
        "max_tokens": 400,
        "temperature": 0.8,
    })

    logger.info(f"✅ Cerebras подключен. Доступно моделей: {len(ai_clients)}")
else:
    logger.warning("⚠️ CEREBRAS_API_KEY не задан! AI-ответы работать не будут.")

# === YouTube ===
youtube = None
if YOUTUBE_API_KEY:
    try:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        logger.info("✅ YouTube API подключен")
    except Exception as e:
        logger.error(f"❌ Ошибка YouTube API: {e}")

# === Spotify ===
spotify = None
try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        creds = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        )
        spotify = spotipy.Spotify(client_credentials_manager=creds)
        logger.info("✅ Spotify API подключен")
    else:
        logger.warning("⚠️ SPOTIFY_CLIENT_ID или SPOTIFY_CLIENT_SECRET не заданы")
except Exception as e:
    logger.error(f"❌ Ошибка Spotify: {e}")