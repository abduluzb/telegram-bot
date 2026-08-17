# music_trailer.py
import os
import tempfile
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import logger
from api_clients import youtube, spotify
from utils import get_ffmpeg_path
import yt_dlp

async def trailer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not youtube:
        await update.message.reply_text("❌ YouTube API не настроен. Добавьте YOUTUBE_API_KEY в .env")
        return

    if not context.args:
        await update.message.reply_text("🎬 Использование: /trailer <название фильма>")
        return

    query = " ".join(context.args)
    status_msg = await update.message.reply_text(f"🔍 Ищу трейлеры: {query}...")

    try:
        request = youtube.search().list(
            part="snippet",
            q=f"{query} trailer",
            type="video",
            maxResults=5,
            order="relevance"
        )
        response = request.execute()
        items = response.get("items", [])

        if not items:
            await status_msg.edit_text(f"❌ Трейлеры к '{query}' не найдены.")
            return

        context.user_data['trailer_videos'] = items

        lines = [f"🎬 *Трейлеры к '{query}':*\n"]
        keyboard = []
        for i, item in enumerate(items, 1):
            title = item["snippet"]["title"]
            lines.append(f"{i}. {title}")
            keyboard.append([InlineKeyboardButton(f"▶️ {i}", callback_data=f"trailer_select_{i-1}")])

        text = "\n".join(lines)
        await status_msg.edit_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Ошибка поиска трейлеров: {e}")
        await status_msg.edit_text("⚠️ Ошибка при поиске трейлеров. Попробуйте позже.")

async def trailer_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("trailer_select_"):
        return

    try:
        index = int(data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Ошибка выбора.")
        return

    items = context.user_data.get('trailer_videos')
    if not items or index >= len(items):
        await query.edit_message_text("❌ Список трейлеров устарел. Попробуйте заново /trailer.")
        return

    item = items[index]
    video_id = item["id"]["videoId"]
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    title = item["snippet"]["title"]

    status_msg = await query.edit_message_text(f"⬇️ Скачиваю трейлер: {title}...")

    ydl_opts = {
        'format': 'best[ext=mp4][filesize<50M]/best[ext=mp4]',
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

    if os.path.exists('cookies.txt'):
        ydl_opts['cookiefile'] = 'cookies.txt'
        logger.info("Используем cookies.txt для YouTube")
    else:
        logger.warning("Файл cookies.txt не найден, возможно потребуется аутентификация")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts['outtmpl'] = os.path.join(tmpdir, 'trailer.%(ext)s')
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                download_task = asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ydl.download([video_url])
                )
                try:
                    await asyncio.wait_for(download_task, timeout=120)
                except asyncio.TimeoutError:
                    await status_msg.edit_text("⏰ Скачивание заняло слишком много времени. Попробуйте позже.")
                    return

                video_file = None
                for f in os.listdir(tmpdir):
                    if f.startswith('trailer.'):
                        video_file = os.path.join(tmpdir, f)
                        break

                if not video_file:
                    logger.error(f"Файлы в tmpdir: {os.listdir(tmpdir)}")
                    await status_msg.edit_text("❌ Не удалось найти скачанный файл.")
                    return

                file_size = os.path.getsize(video_file)
                if file_size > 49 * 1024 * 1024:
                    await status_msg.edit_text(
                        f"📹 *Трейлер:* {title}\n\n"
                        f"⚠️ Файл слишком большой ({file_size // (1024*1024)} МБ). Telegram принимает до 50 МБ.\n"
                        f"🔗 [Смотреть на YouTube]({video_url})",
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    return

                await status_msg.edit_text("📤 Отправляю видео...")
                with open(video_file, 'rb') as f:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=f,
                        caption=f"🎬 *Трейлер:* {title}",
                        supports_streaming=True,
                        parse_mode='Markdown'
                    )
                await status_msg.delete()

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Ошибка скачивания трейлера: {e}")
        await status_msg.edit_text(f"❌ Ошибка при скачивании: {e}\n\nПопробуйте другой трейлер.")
    except Exception as e:
        logger.error(f"Ошибка трейлера: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка: {e}")

async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not spotify:
        await update.message.reply_text("❌ Spotify API не настроен. Проверьте .env")
        return

    if not context.args:
        await update.message.reply_text("🎵 Использование: /music <название песни>")
        return

    query = " ".join(context.args)
    status_msg = await update.message.reply_text(f"🔍 Ищу на Spotify: {query}...")

    try:
        results = spotify.search(q=query, type='track', limit=1)
        tracks = results.get('tracks', {}).get('items', [])

        if not tracks:
            await status_msg.edit_text(f"❌ Ничего не найдено на Spotify.")
            return

        track = tracks[0]
        track_name = track['name']
        artists = ', '.join([a['name'] for a in track['artists']])
        duration = track['duration_ms'] // 1000
        spotify_url = track['external_urls']['spotify']

        context.user_data['music_track_name'] = track_name
        context.user_data['music_artists'] = artists
        context.user_data['music_duration'] = duration
        context.user_data['music_spotify_url'] = spotify_url

        search_query = f"{track_name} {artists} official audio"
        await status_msg.edit_text(f"🔍 Ищу на YouTube: {search_query}...")

        if not youtube:
            await status_msg.edit_text("❌ YouTube API не настроен.")
            return

        request = youtube.search().list(
            part="snippet",
            q=search_query,
            type="video",
            maxResults=5,
            order="relevance"
        )
        response = request.execute()
        items = response.get("items", [])

        if not items:
            await status_msg.edit_text(f"❌ Не найдено видео на YouTube для '{track_name}'.")
            return

        context.user_data['music_youtube_videos'] = items

        lines = [f"🎵 **{track_name}** — {artists}\nВыберите видео для скачивания:\n"]
        keyboard = []
        for i, item in enumerate(items, 1):
            title = item["snippet"]["title"]
            channel = item["snippet"]["channelTitle"]
            lines.append(f"{i}. {title} (канал: {channel})")
            keyboard.append([InlineKeyboardButton(f"▶️ {i}", callback_data=f"music_yt_select_{i-1}")])

        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="music_cancel")])

        text = "\n".join(lines)
        await status_msg.edit_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Ошибка музыки: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {e}")

async def music_yt_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("music_yt_select_"):
        return

    try:
        video_index = int(data.split("_")[3])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Ошибка выбора.")
        return

    videos = context.user_data.get('music_youtube_videos')
    if not videos or video_index >= len(videos):
        await query.edit_message_text("❌ Список видео устарел. Попробуйте заново /music.")
        return

    video = videos[video_index]
    video_id = video["id"]["videoId"]
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    title = video["snippet"]["title"]

    track_name = context.user_data.get('music_track_name', 'Трек')
    artists = context.user_data.get('music_artists', '')
    duration = context.user_data.get('music_duration', 0)

    status_msg = await query.edit_message_text(f"⬇️ Скачиваю аудио: {title}...")

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

    if os.path.exists('cookies.txt'):
        ydl_opts['cookiefile'] = 'cookies.txt'
        logger.info("Используем cookies.txt для YouTube")
    else:
        logger.warning("Файл cookies.txt не найден, возможно потребуется аутентификация")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts['outtmpl'] = os.path.join(tmpdir, '%(title)s.%(ext)s')
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                download_task = asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ydl.download([video_url])
                )
                try:
                    await asyncio.wait_for(download_task, timeout=120)
                except asyncio.TimeoutError:
                    await status_msg.edit_text("⏰ Скачивание заняло слишком много времени. Попробуйте позже.")
                    return

                audio_file = None
                for f in os.listdir(tmpdir):
                    full_path = os.path.join(tmpdir, f)
                    if os.path.isfile(full_path) and os.path.getsize(full_path) > 1024:
                        audio_file = full_path
                        break

                if not audio_file:
                    logger.error(f"Файлы в tmpdir: {os.listdir(tmpdir)}")
                    await status_msg.edit_text("❌ Не удалось найти скачанный файл. Попробуйте другой вариант.")
                    return

                file_size = os.path.getsize(audio_file)
                if file_size > 49 * 1024 * 1024:
                    spotify_url = context.user_data.get('music_spotify_url', '')
                    await status_msg.edit_text(
                        f"🎵 **{track_name}** — {artists}\n\n"
                        f"⚠️ Файл слишком большой ({file_size // (1024*1024)} МБ). Telegram принимает до 50 МБ.\n"
                        f"🔗 [Слушать на Spotify]({spotify_url})",
                        parse_mode='Markdown'
                    )
                    return

                await status_msg.edit_text("📤 Отправляю аудио...")
                with open(audio_file, 'rb') as f:
                    await context.bot.send_audio(
                        chat_id=update.effective_chat.id,
                        audio=f,
                        title=track_name,
                        performer=artists,
                        duration=duration,
                    )
                await status_msg.delete()

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Ошибка yt-dlp: {e}")
        await status_msg.edit_text(f"❌ Ошибка при скачивании: {e}\n\nПопробуйте другой вариант.")
    except Exception as e:
        logger.error(f"Ошибка музыки: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка: {e}")

async def music_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Поиск музыки отменён.")