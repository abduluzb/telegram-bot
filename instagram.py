# instagram.py
import os
import re
import uuid
import tempfile
import asyncio
import aiohttp
from typing import Optional, Tuple

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import SHAZAM_API_KEY, logger
from utils import get_ffmpeg_path
from api_clients import youtube
import yt_dlp

# --- Конфигурация cookies ---
COOKIES_FILE = "www.instagram.com_cookies.txt"
HAS_COOKIES = os.path.exists(COOKIES_FILE)

# Логируем статус cookies при загрузке модуля
if HAS_COOKIES:
    logger.info(f"🍪 Найден файл cookies: {COOKIES_FILE} (будет использован для Instagram/YouTube)")
else:
    logger.warning(f"❌ Файл cookies.txt не найден! Instagram может не дать скачать видео без авторизации.")

# ------------------------------

def is_instagram_url(text: str) -> bool:
    patterns = [
        r'(?:https?:)?\/\/(?:www\.)?instagram\.com\/(?:p|reel|tv)\/[a-zA-Z0-9_-]+',
        r'(?:https?:)?\/\/(?:www\.)?instagram\.com\/stories\/[a-zA-Z0-9_.]+\/[0-9]+',
        r'(?:https?:)?\/\/instagr\.am\/(?:p|reel|tv)\/[a-zA-Z0-9_-]+',
    ]
    for pattern in patterns:
        if re.search(pattern, text):
            return True
    return False

async def download_instagram_video(url: str) -> Optional[str]:
    """Скачивает видео с Instagram, используя cookies, если они есть."""
    temp_dir = tempfile.gettempdir()
    filename = f"instagram_{uuid.uuid4().hex}.mp4"
    filepath = os.path.join(temp_dir, filename)
    
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': filepath,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
        'extractor_args': {
            'youtube': {
                'skip': ['webpage', 'dash', 'hls'],
                'player_client': ['android', 'web'],
            }
        },
        'ignoreerrors': True,
        'nooverwrites': True,
        'timeout': 120,
        'socket_timeout': 120,
    }
    
    # Добавляем cookies, если файл существует
    if HAS_COOKIES:
        ydl_opts['cookiefile'] = COOKIES_FILE
        logger.info(f"🍪 Используем cookies из {COOKIES_FILE} для Instagram")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            download_task = asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ydl.download([url])
            )
            await asyncio.wait_for(download_task, timeout=120)
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 1024:
            return filepath
        else:
            # Поиск других возможных файлов
            for f in os.listdir(temp_dir):
                if f.startswith('instagram_') and f != os.path.basename(filepath):
                    full_path = os.path.join(temp_dir, f)
                    if os.path.isfile(full_path) and os.path.getsize(full_path) > 1024:
                        return full_path
            return None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка скачивания Instagram видео: {error_msg}")
        # Если ошибка связана с cookies, вернём None – обработчик покажет сообщение
        if "cookies" in error_msg.lower() or "empty media" in error_msg.lower():
            logger.error("❌ Возможно, нужны cookies для Instagram.")
        return None

async def download_instagram_audio(url: str) -> Optional[str]:
    """Скачивает аудио из Instagram Reels/видео, используя cookies."""
    ffmpeg_path = get_ffmpeg_path()
    ffmpeg_available = ffmpeg_path is not None

    if not ffmpeg_available:
        logger.warning("ffmpeg не найден, попробуем скачать аудио без конвертации")
    else:
        logger.info(f"ffmpeg найден по пути: {ffmpeg_path}")

    temp_dir = tempfile.gettempdir()
    out_template = os.path.join(temp_dir, 'instagram_audio_%(id)s')

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }] if ffmpeg_available else [],
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
        'extractor_args': {
            'youtube': {
                'skip': ['webpage', 'dash', 'hls'],
                'player_client': ['android', 'web'],
            }
        },
        'ignoreerrors': True,
        'nooverwrites': True,
        'timeout': 120,
        'socket_timeout': 120,
    }

    if ffmpeg_available:
        ydl_opts['ffmpeg_location'] = ffmpeg_path

    if HAS_COOKIES:
        ydl_opts['cookiefile'] = COOKIES_FILE
        logger.info(f"🍪 Используем cookies из {COOKIES_FILE} для Instagram (аудио)")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            download_task = asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ydl.download([url])
            )
            await asyncio.wait_for(download_task, timeout=120)
        
        # Ищем файл с расширением .mp3 или .m4a
        for f in os.listdir(temp_dir):
            if f.startswith('instagram_audio_'):
                full_path = os.path.join(temp_dir, f)
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 1024:
                    if not f.endswith('.mp3') and not ffmpeg_available:
                        return full_path
                    if f.endswith('.mp3'):
                        return full_path
        return None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка скачивания аудио из Instagram: {error_msg}")
        if "cookies" in error_msg.lower() or "empty media" in error_msg.lower():
            logger.error("❌ Возможно, нужны cookies для Instagram.")
        return None

async def recognize_music_shazam(audio_path: str) -> Tuple[Optional[str], Optional[str]]:
    if not SHAZAM_API_KEY:
        logger.error("❌ SHAZAM_API_KEY не найден в .env!")
        return None, None

    base_url = "https://shazam-api.com/api"
    headers = {
        "Authorization": f"Bearer {SHAZAM_API_KEY}"
    }

    try:
        with open(audio_path, 'rb') as f:
            form_data = aiohttp.FormData()
            form_data.add_field('file', f, filename='audio.mp3', content_type='audio/mpeg')
            
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{base_url}/recognize", headers=headers, data=form_data, timeout=30) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"Ошибка распознавания (status {resp.status}): {error_text}")
                        return None, None
                    data = await resp.json()
                    uuid_shazam = data.get('uuid')
                    if not uuid_shazam:
                        logger.error(f"Не получен UUID: {data}")
                        return None, None
                    logger.info(f"Распознавание начато, UUID: {uuid_shazam}")

        attempts = 0
        max_attempts = 20
        async with aiohttp.ClientSession() as session:
            while attempts < max_attempts:
                await asyncio.sleep(2)
                attempts += 1
                try:
                    async with session.post(f"{base_url}/results/{uuid_shazam}", headers=headers, timeout=10) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        status = data.get('status')
                        if status == 'completed':
                            results = data.get('results', [])
                            if results:
                                track = results[0].get('track', {})
                                title = track.get('title')
                                artist = track.get('subtitle')
                                if title and artist:
                                    logger.info(f"🎵 Распознано: {title} - {artist}")
                                    return title, artist
                            logger.warning("Статус completed, но results пусты")
                            return None, None
                        elif status == 'failed':
                            logger.error(f"Распознавание не удалось: {data}")
                            return None, None
                        elif status == 'processing':
                            continue
                except Exception as e:
                    logger.warning(f"Ошибка при опросе (попытка {attempts}): {e}")

        logger.error("Время ожидания результатов истекло")
        return None, None

    except Exception as e:
        logger.error(f"Общая ошибка распознавания: {e}")
        return None, None

async def search_youtube_music(track_name: str, artist: str) -> Optional[str]:
    if not youtube:
        return None
    
    query = f"{track_name} {artist} official audio"
    try:
        request = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            maxResults=1,
            order="relevance"
        )
        response = request.execute()
        items = response.get("items", [])
        if items:
            video_id = items[0]["id"]["videoId"]
            return f"https://www.youtube.com/watch?v={video_id}"
        return None
    except Exception as e:
        logger.error(f"Ошибка поиска на YouTube: {e}")
        return None

# ----------------------------------------------------------------------
# Обработчики колбэков с улучшенными сообщениями об ошибках
# ----------------------------------------------------------------------

async def instagram_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith("ig_audio_"):
        return
    
    audio_id = data.split("_")[2]
    
    requests = context.user_data.get('instagram_audio_requests', {})
    url = requests.get(audio_id)
    if not url:
        await query.message.reply_text("❌ Ссылка устарела. Отправьте видео заново.")
        await query.delete_message()
        return
    
    status_msg = await query.message.reply_text("🎵 Скачиваю аудио из Instagram...")
    
    audio_path = await download_instagram_audio(url)
    
    if audio_path:
        try:
            with open(audio_path, 'rb') as audio_file:
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=audio_file,
                    title="Instagram Reel Audio",
                    performer="Instagram"
                )
            await status_msg.delete()
            await query.delete_message()
        except Exception as e:
            logger.error(f"Ошибка отправки аудио: {e}")
            await status_msg.edit_text(f"❌ Ошибка при отправке аудио: {e}")
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except:
                    pass
    else:
        # Улучшенное сообщение о причине ошибки
        if not HAS_COOKIES:
            await status_msg.edit_text(
                "❌ Не удалось скачать аудио.\n\n"
                "⚠️ Для скачивания с Instagram требуется авторизация.\n"
                "Пожалуйста, экспортируйте cookies с Instagram и сохраните их как `cookies.txt` в папке бота.\n\n"
                "Инструкция:\n"
                "1. Установите расширение для браузера (например, 'Get cookies.txt LOCALLY').\n"
                "2. Войдите в Instagram в браузере.\n"
                "3. Экспортируйте cookies для instagram.com в файл cookies.txt.\n"
                "4. Поместите файл в папку с ботом и перезапустите бота."
            )
        else:
            await status_msg.edit_text(
                "❌ Не удалось скачать аудио.\n\n"
                "Возможные причины:\n"
                "- Видео недоступно\n"
                "- Не установлен ffmpeg (нужен для извлечения аудио)\n"
                "- Ссылка ведёт на приватный аккаунт\n"
                "- Cookies устарели (обновите файл cookies.txt)\n\n"
                "Установите ffmpeg: apt-get install ffmpeg"
            )

async def instagram_find_full_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith("ig_find_full_"):
        return
    
    audio_id = data.split("_")[3]
    
    requests = context.user_data.get('instagram_audio_requests', {})
    url = requests.get(audio_id)
    if not url:
        await query.message.reply_text("❌ Ссылка устарела. Отправьте видео заново.")
        await query.delete_message()
        return
    
    status_msg = await query.message.reply_text("🔍 Скачиваю аудио для распознавания...")
    
    audio_path = await download_instagram_audio(url)
    if not audio_path:
        if not HAS_COOKIES:
            await status_msg.edit_text(
                "❌ Не удалось скачать аудио из видео.\n\n"
                "⚠️ Для скачивания с Instagram требуется авторизация.\n"
                "Поместите файл cookies.txt с экспортированными cookies Instagram в папку бота."
            )
        else:
            await status_msg.edit_text(
                "❌ Не удалось скачать аудио из видео.\n\n"
                "Возможно, ссылка недоступна, файл cookies устарел или аккаунт приватный."
            )
        return
    
    await status_msg.edit_text("🎵 Распознаю музыку через Shazam...")
    track_name, artist = await recognize_music_shazam(audio_path)
    
    if audio_path and os.path.exists(audio_path):
        try:
            os.remove(audio_path)
        except:
            pass
    
    if not track_name or not artist:
        await status_msg.edit_text(
            "❌ Не удалось распознать музыку.\n\n"
            "Возможные причины:\n"
            "- Аудио слишком короткое или низкое качество\n"
            "- Музыка редкая или не в базе Shazam\n"
            "- Достигнут лимит запросов"
        )
        return
    
    await status_msg.edit_text(f"🎶 Найдено: {track_name} - {artist}\n🔍 Ищу полную версию на YouTube...")
    video_url = await search_youtube_music(track_name, artist)
    
    if not video_url:
        await status_msg.edit_text(
            f"🎶 Найдено: {track_name} - {artist}\n\n"
            "❌ Не удалось найти полную версию на YouTube.\n"
            "Попробуйте поискать вручную."
        )
        return
    
    await status_msg.edit_text(f"⬇️ Скачиваю: {track_name} - {artist}...")
    
    ffmpeg_path = get_ffmpeg_path()
    ffmpeg_available = ffmpeg_path is not None

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }] if ffmpeg_available else [],
        'outtmpl': '%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
        'extractor_args': {
            'youtube': {
                'skip': ['webpage', 'dash', 'hls'],
                'player_client': ['android', 'web'],
            }
        },
        'ignoreerrors': True,
        'nooverwrites': True,
        'timeout': 120,
        'socket_timeout': 120,
    }

    if ffmpeg_available:
        ydl_opts['ffmpeg_location'] = ffmpeg_path

    # Для YouTube также используем cookies, если они есть
    if HAS_COOKIES:
        ydl_opts['cookiefile'] = COOKIES_FILE
        logger.info("🍪 Используем cookies.txt для YouTube (полная версия)")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts['outtmpl'] = os.path.join(tmpdir, '%(title)s.%(ext)s')
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                download_task = asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ydl.download([video_url])
                )
                await asyncio.wait_for(download_task, timeout=120)
                
                audio_file = None
                for f in os.listdir(tmpdir):
                    full_path = os.path.join(tmpdir, f)
                    if os.path.isfile(full_path) and os.path.getsize(full_path) > 1024:
                        audio_file = full_path
                        break
                
                if not audio_file:
                    await status_msg.edit_text("❌ Не удалось найти скачанный файл.")
                    return
                
                await status_msg.edit_text("📤 Отправляю полную версию...")
                with open(audio_file, 'rb') as f:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=f,
                        title=track_name,
                        performer=artist,
                    )
                await status_msg.delete()
                await query.delete_message()
                
    except Exception as e:
        logger.error(f"Ошибка скачивания полной версии: {e}")
        await status_msg.edit_text(f"❌ Ошибка при скачивании: {e}")
    
    if audio_id in context.user_data.get('instagram_audio_requests', {}):
        del context.user_data['instagram_audio_requests'][audio_id]