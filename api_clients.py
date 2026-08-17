# api_clients.py
from config import CEREBRAS_API_KEY, YOUTUBE_API_KEY, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, MODELS, logger
from cerebras.cloud.sdk import Cerebras
from googleapiclient.discovery import build
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

client = Cerebras(api_key=CEREBRAS_API_KEY)
logger.info(f"✅ Cerebras API настроен. Моделей: {len(MODELS)}")

youtube = None
if YOUTUBE_API_KEY:
    try:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        logger.info("✅ YouTube API подключен")
    except Exception as e:
        logger.error(f"❌ Ошибка YouTube API: {e}")

spotify = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        client_credentials_manager = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        )
        spotify = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
        logger.info("✅ Spotify API подключен")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения Spotify: {e}")
else:
    logger.warning("⚠️ SPOTIFY_CLIENT_ID или SPOTIFY_CLIENT_SECRET не заданы")