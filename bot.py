# bot.py - Luna AI с трейлерами (MP4), выбором музыки, админ-панелью и всеми функциями
# Увеличенные таймауты, обработка больших файлов

import os
import asyncio
import logging
import time
import re
import aiohttp
import io
import requests
import base64
import tempfile
import yt_dlp
from typing import Dict, List, Set, Tuple, Optional
from datetime import datetime, timedelta
import pytz

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from dotenv import load_dotenv
from telegram import Update, Chat, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, ReactionTypeEmoji
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telegram.helpers import escape_markdown
from cerebras.cloud.sdk import Cerebras
from googleapiclient.discovery import build

# Spotify
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from database import (
    init_db,
    get_global_mode,
    set_global_mode,
    update_user_stats,
    get_user_stats,
    add_chat_memory,
    get_chat_memory,
    clear_chat_memory,
    get_violations,
    update_violation,
    clear_violation,
    add_reminder,
    get_due_reminders,
    delete_reminder,
    get_or_create_user_info,
    update_user_city_timezone,
    update_user_custom_name,
    add_note,
    get_notes,
    delete_note,
    clear_table,
    get_user_history,
    get_session,
    UserStats,
    UserInfo,
    ChatMemory,
    Violation,
    Reminder,
    Note,
    Config,
    TrainingData,
    DeletedMessage,
    DailyStats,
    ReactionLog,
    save_training_pair,
    find_training_answer,
    log_deleted_message,
    update_daily_stats,
    get_detailed_stats,
    get_top_users,
    get_reaction_for_text,
    search_music,
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не найден!")
if not CEREBRAS_API_KEY:
    raise ValueError("❌ CEREBRAS_API_KEY не найден!")

OWNER_USER_ID = int(os.getenv("OWNER_USER_ID")) if os.getenv("OWNER_USER_ID") else None
AUTO_MODERATION_ENABLED = True

pending_requests = {}
OWNER_NAME = None
OWNER_DESCRIPTION = "парень с карими глазами, высокий, красивый, умный и обаятельный"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Cerebras ===
client = Cerebras(api_key=CEREBRAS_API_KEY)
MODELS = ["gpt-oss-120b", "zai-glm-4.7"]
logger.info(f"✅ Cerebras API настроен. Моделей: {len(MODELS)}")

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

# === Хранилища ===
chat_members: Dict[int, Set[int]] = {}
user_names: Dict[int, str] = {}
last_request_time: Dict[int, float] = {}
user_memory: Dict[int, List[Dict]] = {}
MAX_MEMORY = 50

WAITING_TEXT, WAITING_PHOTO, CONFIRM = range(3)

# === Вспомогательные функции ===
def get_user_timezone(timezone_str: str):
    if not timezone_str:
        return None
    tz_str = timezone_str.strip()
    match = re.match(r'(?i)(utc|gmt)\s*([+-]?\d{1,2}(?::\d{2})?)$', tz_str)
    if match:
        offset_str = match.group(2)
        try:
            if ':' in offset_str:
                hours, minutes = map(int, offset_str.split(':'))
            else:
                hours = int(offset_str)
                minutes = 0
            delta = timedelta(hours=hours, minutes=minutes)
            return datetime.timezone(delta)
        except:
            pass
    if ZoneInfo:
        try:
            return ZoneInfo(tz_str)
        except:
            pass
    try:
        return pytz.timezone(tz_str)
    except:
        pass
    return None

def is_owner(user_id: int) -> bool:
    if OWNER_USER_ID is None:
        return True
    return user_id == OWNER_USER_ID

def get_chat_members(chat_id: int) -> Set[int]:
    if chat_id not in chat_members:
        chat_members[chat_id] = set()
    return chat_members[chat_id]

def add_chat_member(chat_id: int, user_id: int, user_name: str):
    members = get_chat_members(chat_id)
    members.add(user_id)
    if user_id not in user_names:
        user_names[user_id] = user_name

def get_user_memory(user_id: int) -> List[Dict]:
    if user_id not in user_memory:
        user_memory[user_id] = []
    return user_memory[user_id]

def add_to_user_memory(user_id: int, text: str, role: str = "user"):
    memory = get_user_memory(user_id)
    memory.append({"role": role, "text": text})
    if len(memory) > MAX_MEMORY:
        memory.pop(0)

def clear_memory(user_id: int, chat_id: int = None):
    if user_id in user_memory:
        user_memory[user_id] = []
    if chat_id and chat_id < 0:
        clear_chat_memory(chat_id)

def build_context(chat_id: int, user_id: int, user_name: str, custom_name: str = None) -> str:
    user_history = get_user_history(user_id, limit=30)
    user_hist = get_user_memory(user_id)
    members = get_chat_members(chat_id)
    parts = []
    if custom_name:
        parts.append(f"=== Твоё имя: {custom_name} ===")
        parts.append("Обращайся к пользователю по этому имени.")
    if user_history:
        parts.append("=== История твоих сообщений (из БД) ===")
        for msg in user_history:
            parts.append(f"{msg['user_name']}: {msg['text']}")
        parts.append("")
    if user_hist:
        parts.append("=== Твоя краткосрочная память ===")
        for msg in user_hist[-5:]:
            role = "Ты" if msg["role"] == "user" else "Я"
            parts.append(f"{role}: {msg['text']}")
        parts.append("")
    parts.append(f"=== Информация ===")
    parts.append(f"Участников: {len(members)}")
    parts.append(f"Пользователь: {user_name}")
    return "\n".join(parts)

async def notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str):
    if OWNER_USER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_USER_ID, text=text)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление владельцу: {e}")

# ===== МОДЕРАЦИЯ =====
BAD_WORDS = [
    "хуй", "пизда", "блядь", "ёб", "еба", "ебан", "мудак", "гандон", "пидор",
    "сучка", "сука", "жопа", "залупа", "хуйня", "пиздец", "хуесос", "мразь",
    "тварь", "шлюха", "бля", "нахуй", "охуел", "ахуел", "ебать", "ебнуть",
    "заебал", "заебало", "выблядок", "уебан", "хуйло", "пидр", "гей"
]

def contains_bad_words(text: str) -> bool:
    text_lower = text.lower()
    for word in BAD_WORDS:
        if word in text_lower:
            return True
    return False

def get_ban_duration(violation_count: int) -> int:
    if violation_count == 1:
        return 0
    elif violation_count == 2:
        return 5 * 60
    elif violation_count == 3:
        return 60 * 60
    else:
        return 24 * 60 * 60

def format_time(seconds: int) -> str:
    if seconds < 60:
        return f"⏱️ {seconds} секунд"
    elif seconds < 3600:
        return f"⏱️ {seconds//60} минут"
    elif seconds < 86400:
        return f"⏱️ {seconds//3600} часов"
    else:
        return f"⏱️ {seconds//86400} дней"

user_violations: Dict[int, Dict] = {}

async def apply_moderation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    global AUTO_MODERATION_ENABLED
    if not AUTO_MODERATION_ENABLED:
        return False
    message = update.effective_message
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    if chat_type not in [Chat.GROUP, Chat.SUPERGROUP]:
        return False
    if is_owner(user_id):
        return False
    text = message.text or ""
    if not contains_bad_words(text):
        return False
    if user_id in user_violations:
        ban_until = user_violations[user_id].get("ban_until", 0)
        if ban_until > time.time():
            try:
                await message.delete()
            except:
                pass
            return True
    if user_id not in user_violations:
        user_violations[user_id] = {"count": 0, "ban_until": 0, "chat_id": chat_id}
    violations = user_violations[user_id]
    violations["count"] += 1
    violations["chat_id"] = chat_id
    ban_duration = get_ban_duration(violations["count"])
    if ban_duration == 0:
        try:
            await message.reply_text(
                f"⚠️ {update.effective_user.first_name}, это предупреждение! Нарушение #{violations['count']}"
            )
            await message.delete()
        except:
            pass
    else:
        ban_until = time.time() + ban_duration
        violations["ban_until"] = ban_until
        try:
            await context.bot.ban_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                until_date=datetime.fromtimestamp(ban_until)
            )
            time_str = format_time(ban_duration)
            ban_end_time = datetime.fromtimestamp(ban_until).strftime('%Y-%m-%d %H:%M:%S')
            msg = (
                f"🚫 {update.effective_user.first_name} **забанен** на {time_str}\n"
                f"📊 Нарушение #{violations['count']}\n"
                f"🕐 До: {ban_end_time}"
            )
            await message.reply_text(msg, parse_mode='Markdown')
            await message.delete()
            owner_msg = (
                f"🔔 **Автоматический бан**\n"
                f"👤 Пользователь: {update.effective_user.first_name} (ID: {user_id})\n"
                f"⏳ Длительность: {time_str}\n"
                f"🕐 До: {ban_end_time}\n"
                f"📊 Нарушение #{violations['count']}\n"
                f"💬 Сообщение: {text[:50]}..."
            )
            await notify_owner(context, owner_msg)
        except Exception as e:
            logger.error(f"Ошибка бана: {e}")
            await message.reply_text(f"❌ Не удалось забанить пользователя: {e}")
    return True

# ===== НАПОМИНАНИЯ =====
def parse_time(text: str) -> Tuple[Optional[float], str]:
    text_lower = text.lower()
    match = re.search(r'(\d+)\s*(м|мин|с|сек|ч|час|д|день|дня|дней)', text_lower)
    if not match:
        return None, text
    value = int(match.group(1))
    unit = match.group(2)
    seconds = 0
    if unit in ('м', 'мин'):
        seconds = value * 60
    elif unit in ('с', 'сек'):
        seconds = value
    elif unit in ('ч', 'час'):
        seconds = value * 3600
    elif unit in ('д', 'день', 'дня', 'дней'):
        seconds = value * 86400
    if seconds == 0:
        return None, text
    clean_text = re.sub(r'\d+\s*(м|мин|с|сек|ч|час|д|день|дня|дней)', '', text_lower).strip()
    if not clean_text:
        clean_text = "Напоминание"
    return time.time() + seconds, clean_text

async def check_reminders(application: Application):
    try:
        while True:
            try:
                current_time = time.time()
                due = get_due_reminders(datetime.fromtimestamp(current_time))
                for item in due:
                    try:
                        await application.bot.send_message(
                            chat_id=item['chat_id'],
                            text=f"⏰ Напоминание: {item['text']}"
                        )
                    except:
                        pass
                    delete_reminder(item['id'])
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except:
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass

# ===== ВИКИПЕДИЯ =====
async def get_wikipedia_summary(query: str, lang: str = "ru") -> Optional[str]:
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "titles": query,
        "redirects": 1
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                pages = data.get("query", {}).get("pages", {})
                for page_id, page in pages.items():
                    if page_id == "-1":
                        return None
                    extract = page.get("extract", "").strip()
                    if extract:
                        if len(extract) > 1000:
                            extract = extract[:1000] + "..."
                        return extract
                return None
    except Exception as e:
        logger.error(f"Ошибка Wikipedia API: {e}")
        return None

# ===== GITHUB ФУНКЦИИ =====
def search_github_code(query: str) -> Optional[List[Dict]]:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    url = "https://api.github.com/search/code"
    params = {
        "q": f"{query}+repo:{GITHUB_REPO}",
        "per_page": 10
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            logger.error(f"GitHub API error: {response.status_code} - {response.text}")
            return None
        data = response.json()
        items = data.get("items", [])
        results = []
        for item in items:
            file_path = item.get("path")
            html_url = item.get("html_url")
            results.append({"path": file_path, "url": html_url})
        return results
    except Exception as e:
        logger.error(f"Ошибка поиска в GitHub: {e}")
        return None

def get_github_file_content(file_path: str) -> Optional[str]:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            logger.error(f"GitHub API error: {response.status_code} - {response.text}")
            return None
        data = response.json()
        content = data.get("content", "")
        if content:
            decoded = base64.b64decode(content).decode("utf-8")
            return decoded
        return None
    except Exception as e:
        logger.error(f"Ошибка получения файла: {e}")
        return None

# ===== АДМИН-ПАНЕЛЬ =====
def get_admin_keyboard(text_set: bool = False, photo_set: bool = False) -> InlineKeyboardMarkup:
    keyboard = []
    row1 = []
    if text_set:
        row1.append(InlineKeyboardButton("✅ Текст задан", callback_data="admin_text_set"))
    else:
        row1.append(InlineKeyboardButton("✏️ Написать текст", callback_data="admin_write_text"))
    if photo_set:
        row1.append(InlineKeyboardButton("✅ Фото добавлено", callback_data="admin_photo_set"))
    else:
        row1.append(InlineKeyboardButton("🖼️ Прикрепить фото", callback_data="admin_add_photo"))
    keyboard.append(row1)

    row2 = []
    if text_set or photo_set:
        row2.append(InlineKeyboardButton("👀 Предпросмотр", callback_data="admin_preview"))
        row2.append(InlineKeyboardButton("📨 Отправить!", callback_data="admin_send"))
    keyboard.append(row2)

    row3 = [
        InlineKeyboardButton("🗑️ Очистить всё", callback_data="admin_clear"),
        InlineKeyboardButton("🔙 Закрыть", callback_data="admin_close"),
    ]
    keyboard.append(row3)

    return InlineKeyboardMarkup(keyboard)

async def admin_panel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return ConversationHandler.END

    context.user_data['admin_text'] = None
    context.user_data['admin_photo'] = None
    context.user_data['admin_photo_file_id'] = None

    text = (
        "👑 *Админ-панель Luna AI*\n\n"
        "Здесь вы можете подготовить рассылку для всех чатов.\n"
        "1️⃣ Напишите текст (нажмите кнопку)\n"
        "2️⃣ Прикрепите фото (опционально)\n"
        "3️⃣ Отправьте рассылку\n\n"
        "Текущий статус:"
    )
    status = "📝 Текст: не задан\n🖼️ Фото: нет"
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text + "\n\n" + status,
        reply_markup=get_admin_keyboard(False, False),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await query.edit_message_text("⛔ Доступ запрещён.")
        return ConversationHandler.END

    data = query.data
    user_data = context.user_data

    if data == "admin_write_text":
        await query.edit_message_text(
            "✏️ *Введите текст рассылки*\n\n"
            "Просто напишите сообщение в этот чат. Я сохраню его.\n"
            "Чтобы отменить, нажмите /cancel",
            parse_mode='Markdown'
        )
        return WAITING_TEXT

    elif data == "admin_add_photo":
        await query.edit_message_text(
            "🖼️ *Прикрепите фото*\n\n"
            "Отправьте мне фото (одно). Я сохраню его.\n"
            "Чтобы пропустить, нажмите /skip",
            parse_mode='Markdown'
        )
        return WAITING_PHOTO

    elif data == "admin_clear":
        user_data['admin_text'] = None
        user_data['admin_photo'] = None
        user_data['admin_photo_file_id'] = None
        await query.edit_message_text(
            "🗑️ *Все данные очищены.*\n\nВозвращаюсь в панель.",
            reply_markup=get_admin_keyboard(False, False),
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    elif data == "admin_preview":
        text = user_data.get('admin_text', '')
        photo = user_data.get('admin_photo_file_id')
        if not text and not photo:
            await query.edit_message_text(
                "❌ Нет данных для предпросмотра.\nЗадайте текст или добавьте фото.",
                reply_markup=get_admin_keyboard(False, False)
            )
            return ConversationHandler.END
        preview_text = "👀 *Предпросмотр рассылки*\n\n"
        if text:
            preview_text += f"📝 *Текст:*\n{text}\n\n"
        if photo:
            preview_text += "🖼️ *Фото:* прикреплено"
        if photo:
            await query.message.reply_photo(
                photo=photo,
                caption=preview_text,
                parse_mode=None
            )
        else:
            await query.message.reply_text(preview_text, parse_mode='Markdown')
        await query.answer()
        return ConversationHandler.END

    elif data == "admin_send":
        text = user_data.get('admin_text', '')
        photo = user_data.get('admin_photo_file_id')
        if not text and not photo:
            await query.edit_message_text("❌ Нет данных для отправки.", reply_markup=get_admin_keyboard(False, False))
            return ConversationHandler.END

        all_chats = list(chat_members.keys())
        if not all_chats:
            await query.edit_message_text("📭 Нет известных чатов.", reply_markup=get_admin_keyboard(False, False))
            return ConversationHandler.END

        status_msg = await query.edit_message_text(f"⏳ Отправляю рассылку в {len(all_chats)} чатов...")
        success = 0
        errors = 0

        for cid in all_chats:
            try:
                if photo:
                    await context.bot.send_photo(
                        chat_id=cid,
                        photo=photo,
                        caption=text if text else None,
                        parse_mode=None
                    )
                else:
                    await context.bot.send_message(
                        chat_id=cid,
                        text=text,
                        parse_mode='Markdown'
                    )
                success += 1
            except Exception as e:
                logger.error(f"Ошибка отправки в чат {cid}: {e}")
                errors += 1
            await asyncio.sleep(0.1)

        if OWNER_USER_ID and OWNER_USER_ID not in all_chats:
            try:
                if photo:
                    await context.bot.send_photo(
                        chat_id=OWNER_USER_ID,
                        photo=photo,
                        caption=f"📢 Копия рассылки:\n{text}" if text else "📢 Копия рассылки (фото)",
                        parse_mode=None
                    )
                else:
                    await context.bot.send_message(
                        chat_id=OWNER_USER_ID,
                        text=f"📢 Копия рассылки:\n\n{text}",
                        parse_mode='Markdown'
                    )
                success += 1
            except:
                pass

        await status_msg.edit_text(
            f"✅ Рассылка завершена.\n"
            f"📨 Успешно: {success}\n"
            f"❌ Ошибок: {errors}",
            reply_markup=get_admin_keyboard(False, False)
        )
        user_data['admin_text'] = None
        user_data['admin_photo'] = None
        user_data['admin_photo_file_id'] = None
        return ConversationHandler.END

    elif data == "admin_close":
        await query.edit_message_text("🔙 Панель закрыта.")
        return ConversationHandler.END

    elif data in ("admin_text_set", "admin_photo_set"):
        await query.answer("Уже задано")
        return ConversationHandler.END

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return ConversationHandler.END

    text = update.message.text
    if text.startswith('/'):
        await update.message.reply_text("❌ Команды не принимаются. Напишите текст.")
        return WAITING_TEXT

    context.user_data['admin_text'] = text
    await update.message.reply_text(
        f"✅ Текст сохранён:\n\n{text[:200]}{'...' if len(text)>200 else ''}\n\n"
        "Возвращаюсь в панель.",
        reply_markup=get_admin_keyboard(True, bool(context.user_data.get('admin_photo_file_id')))
    )
    return ConversationHandler.END

async def handle_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return ConversationHandler.END

    photo = update.message.photo
    if not photo:
        await update.message.reply_text("❌ Отправьте фото (не документ). Попробуйте снова.")
        return WAITING_PHOTO

    photo_file = photo[-1]
    context.user_data['admin_photo_file_id'] = photo_file.file_id
    context.user_data['admin_photo'] = photo_file

    await update.message.reply_text(
        "✅ Фото сохранено.\n\nВозвращаюсь в панель.",
        reply_markup=get_admin_keyboard(bool(context.user_data.get('admin_text')), True)
    )
    return ConversationHandler.END

async def skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return ConversationHandler.END

    await update.message.reply_text(
        "⏭️ Фото пропущено. Возвращаюсь в панель.",
        reply_markup=get_admin_keyboard(bool(context.user_data.get('admin_text')), False)
    )
    return ConversationHandler.END

async def cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return ConversationHandler.END

    await update.message.reply_text(
        "❌ Отменено. Возвращаюсь в панель.",
        reply_markup=get_admin_keyboard(
            bool(context.user_data.get('admin_text')),
            bool(context.user_data.get('admin_photo_file_id'))
        )
    )
    return ConversationHandler.END

# ===== КОМАНДЫ =====
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    user_info = get_or_create_user_info(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code
    )
    custom_name = user_info.get('custom_name') if user_info else None
    greeting = f"🌙 Привет! Я Luna AI — самый быстрый AI-ассистент.\n"
    if custom_name:
        greeting += f"Рада снова видеть тебя, {custom_name}! "
    else:
        greeting += "Ты можешь сказать «луна запомни моё имя <имя>», чтобы я обращалась к тебе по имени.\n"
    greeting += (
        "Умею анализировать эмоции, давать погоду, напоминать,\n"
        "генерировать картинки, искать видео на YouTube и искать информацию в Википедии!\n\n"
        "🎬 *Новое!* Трейлеры фильмов — команда /trailer <название> (скачиваю MP4)\n"
        "🎵 *Новое!* Поиск музыки с выбором трека — /music <название>\n\n"
        "Мои команды:\n"
        "/setcity <город> – указать свой город\n"
        "/settimezone <таймзона> – указать часовой пояс\n"
        "/weather – погода (если город задан)\n"
        "Скажи «луна запомни <текст>» – я сохраню заметку.\n"
        "/notes – показать последние заметки\n"
        "/reset – очистить историю чата (в БД)\n"
        "/admin – админ-панель (только для владельца)\n\n"
        "Нажми на кнопки ниже, чтобы попробовать:"
    )
    await update.message.reply_text(greeting, reply_markup=get_main_menu_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Все команды", callback_data="all_commands")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
    ])
    await update.message.reply_text(
        "🌙 Luna AI на Cerebras.\n"
        "• Отвечаю, когда упоминают @bot или пишут 'луна'\n"
        "• Помню контекст чата (только твои сообщения)\n"
        "• Генерирую изображения через /imagine\n"
        "• Ищу видео через /yt\n"
        "• Ищу информацию в Википедии через /wiki\n"
        "• Сохраняю заметки по команде 'луна запомни ...'\n"
        "• Запоминаю твоё имя по команде 'луна запомни моё имя <имя>'\n"
        "• 🎬 Поиск и скачивание трейлеров через /trailer\n"
        "• 🎵 Поиск музыки с выбором через /music\n"
        "• Команды: /weather, /imagine, /yt, /remind, /reset, /members, /warn, /unban, /setmoderation, /setmode, /getmode, /wiki, /owners, /setcity, /settimezone, /notes, /delnote, /broadcast, /admin, /stats_detail, /music, /trailer\n"
        "• Владельцу:\n"
        "   • 'луна очисти таблицу <имя>' – очистить таблицу\n"
        "   • 'луна искать в коде <текст>' – поиск в GitHub\n"
        "   • 'луна показать файл <путь>' – показать файл\n"
        "   • 'луна объясни файл <путь>' – AI-объяснение файла\n"
        "• /setmode <fast|smart|sarcastic|flirt|auto> — глобальный режим\n"
        "• /admin — открыть админ-панель для рассылки\n"
        "• /stats_detail — подробная статистика (владелец)\n"
        "• /music — поиск и выбор музыки\n"
        "• /trailer — поиск и скачивание трейлеров",
        reply_markup=keyboard
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await admin_panel_start(update, context)

async def setmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Только владелец может менять глобальный режим.")
        return
    if not context.args:
        current = get_global_mode()
        await update.message.reply_text(
            f"Текущий режим: {current}\n"
            "Использование: /setmode <fast|smart|sarcastic|flirt|auto>"
        )
        return
    mode = context.args[0].lower()
    valid_modes = ["fast", "smart", "sarcastic", "flirt", "auto"]
    if mode not in valid_modes:
        await update.message.reply_text("Некорректный режим. Доступны: fast, smart, sarcastic, flirt, auto")
        return
    set_global_mode(mode)
    logger.info(f"Владелец установил глобальный режим: {mode}")
    await update.message.reply_text(f"✅ Глобальный режим установлен на: {mode}")

async def getmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = get_global_mode()
    await update.message.reply_text(f"🌙 Текущий глобальный режим: {current}")

async def set_moderation_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Только владелец может управлять модерацией.")
        return
    global AUTO_MODERATION_ENABLED
    if not context.args:
        await update.message.reply_text(
            "Использование: /setmoderation on/off\n"
            f"Текущее состояние: {'✅ Включена' if AUTO_MODERATION_ENABLED else '❌ Выключена'}"
        )
        return
    action = context.args[0].lower()
    if action == 'on':
        AUTO_MODERATION_ENABLED = True
        await update.message.reply_text("✅ Автоматическая модерация включена.")
    elif action == 'off':
        AUTO_MODERATION_ENABLED = False
        await update.message.reply_text("❌ Автоматическая модерация выключена.")
    else:
        await update.message.reply_text("Некорректное значение. Используйте on или off.")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Только владелец может использовать эту команду.")
        return

    text = update.message.caption or update.message.text or ""
    text = re.sub(r'^/broadcast\s*', '', text).strip()
    photo = update.message.photo[-1] if update.message.photo else None

    if not text and not photo:
        await update.message.reply_text(
            "❌ Напишите текст для рассылки после команды или прикрепите фото.\n"
            "Пример: /broadcast Всем привет! (с фото или без)"
        )
        return

    all_chats = list(chat_members.keys())
    if not all_chats:
        await update.message.reply_text("📭 Нет известных чатов.")
        return

    status_msg = await update.message.reply_text(f"⏳ Начинаю рассылку в {len(all_chats)} чатов...")
    success = 0
    errors = 0

    for cid in all_chats:
        try:
            if photo:
                await context.bot.send_photo(
                    chat_id=cid,
                    photo=photo.file_id,
                    caption=text if text else None,
                    parse_mode=None
                )
            else:
                await context.bot.send_message(
                    chat_id=cid,
                    text=text,
                    parse_mode='Markdown'
                )
            success += 1
        except Exception as e:
            logger.error(f"Ошибка отправки в чат {cid}: {e}")
            errors += 1
        await asyncio.sleep(0.1)

    if OWNER_USER_ID and OWNER_USER_ID not in all_chats:
        try:
            if photo:
                await context.bot.send_photo(
                    chat_id=OWNER_USER_ID,
                    photo=photo.file_id,
                    caption=f"📢 Копия рассылки:\n{text}" if text else "📢 Копия рассылки (фото)",
                    parse_mode=None
                )
            else:
                await context.bot.send_message(
                    chat_id=OWNER_USER_ID,
                    text=f"📢 Копия рассылки:\n\n{text}",
                    parse_mode='Markdown'
                )
            success += 1
        except:
            pass

    await status_msg.edit_text(
        f"✅ Рассылка завершена.\n"
        f"📨 Успешно: {success}\n"
        f"❌ Ошибок: {errors}"
    )

# ===== НОВЫЕ КОМАНДЫ =====
async def stats_detail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Только владелец.")
        return
    update_daily_stats()
    stats = get_detailed_stats(7)
    if not stats:
        await update.message.reply_text("📊 Статистика пока пуста.")
        return
    lines = ["📊 *Статистика за последние 7 дней:*"]
    total_messages = 0
    total_users = 0
    for s in stats:
        lines.append(f"📅 {s.date.strftime('%Y-%m-%d')}: {s.total_messages} сообщений, {s.unique_users} пользователей, {s.active_chats} чатов")
        total_messages += s.total_messages
        total_users += s.unique_users
    lines.append(f"\n📌 *Итого за 7 дней:* {total_messages} сообщений, ~{total_users//7} пользователей в день")
    top = get_top_users(5)
    if top:
        lines.append("\n🏆 *Топ-5 активных пользователей:*")
        for i, u in enumerate(top, 1):
            name = u['first_name'] or u['username'] or str(u['user_id'])
            lines.append(f"{i}. {name} – {u['messages']} сообщений")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

# === КОМАНДА ТРЕЙЛЕРОВ (скачивание MP4) ===
async def trailer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск трейлеров фильмов на YouTube и скачивание MP4."""
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

        # Сохраняем список видео в user_data для обработки выбора
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

# === ОБРАБОТЧИК ВЫБОРА ТРЕЙЛЕРА (скачивание MP4) ===
async def trailer_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скачивает выбранный трейлер в MP4 и отправляет."""
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

    # Настройки yt-dlp для MP4 (высокое качество, но с ограничением размера)
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
                'player_client': ['android', 'web'],
                'skip': ['hls', 'dash'],
            }
        },
        'ignoreerrors': True,
        'nooverwrites': True,
        'timeout': 120,          # увеличенный таймаут
        'socket_timeout': 120,
    }

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts['outtmpl'] = os.path.join(tmpdir, '%(title)s.%(ext)s')
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Запускаем скачивание с увеличенным таймаутом
                download_task = asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ydl.download([video_url])
                )
                try:
                    await asyncio.wait_for(download_task, timeout=120)
                except asyncio.TimeoutError:
                    await status_msg.edit_text("⏰ Скачивание заняло слишком много времени. Попробуйте позже.")
                    return

                # Находим скачанный файл
                video_file = None
                for f in os.listdir(tmpdir):
                    if f.endswith('.mp4'):
                        video_file = os.path.join(tmpdir, f)
                        break
                if not video_file:
                    await status_msg.edit_text("❌ Не удалось найти скачанный файл.")
                    return

                # Проверяем размер файла
                file_size = os.path.getsize(video_file)
                if file_size > 49 * 1024 * 1024:  # 49 МБ
                    await status_msg.edit_text(
                        f"📹 *Трейлер:* {title}\n\n"
                        f"⚠️ Файл слишком большой ({file_size // (1024*1024)} МБ). Telegram принимает до 50 МБ.\n"
                        f"🔗 [Смотреть на YouTube]({video_url})",
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    return

                # Отправляем видео
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

# === УЛУЧШЕННАЯ КОМАНДА MUSIC (с выбором трека и видео) ===
async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск музыки через Spotify с выбором трека."""
    if not spotify:
        await update.message.reply_text("❌ Spotify API не настроен. Проверьте .env")
        return

    if not context.args:
        await update.message.reply_text("🎵 Использование: /music <название песни>")
        return

    query = " ".join(context.args)
    status_msg = await update.message.reply_text(f"🔍 Ищу на Spotify: {query}...")

    try:
        results = spotify.search(q=query, type='track', limit=5)
        tracks = results.get('tracks', {}).get('items', [])

        if not tracks:
            await status_msg.edit_text(f"❌ Ничего не найдено.")
            return

        context.user_data['music_tracks'] = tracks

        lines = [f"🎵 *Найдено {len(tracks)} треков:*\n"]
        keyboard = []
        for i, track in enumerate(tracks):
            name = track['name']
            artists = ', '.join([a['name'] for a in track['artists']])
            duration_ms = track['duration_ms']
            minutes = duration_ms // 60000
            seconds = (duration_ms % 60000) // 1000
            lines.append(f"{i+1}. **{name}** — {artists} ({minutes}:{seconds:02d})")
            keyboard.append([InlineKeyboardButton(f"🎵 {i+1}", callback_data=f"music_select_{i}")])

        text = "\n".join(lines)
        await status_msg.edit_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Ошибка Spotify API: {e}")
        await status_msg.edit_text(f"❌ Ошибка при поиске: {e}")

# === НОВЫЙ ОБРАБОТЧИК ВЫБОРА ТРЕКА (поиск видео на YouTube) ===
async def music_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """После выбора трека из Spotify – ищем видео на YouTube и предлагаем выбрать."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("music_select_"):
        return

    try:
        track_index = int(data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Ошибка выбора.")
        return

    tracks = context.user_data.get('music_tracks')
    if not tracks or track_index >= len(tracks):
        await query.edit_message_text("❌ Список треков устарел. Попробуйте заново /music.")
        return

    track = tracks[track_index]
    track_name = track['name']
    artists = ', '.join([a['name'] for a in track['artists']])

    # Ищем видео на YouTube
    search_query = f"{track_name} {artists} official audio"
    status_msg = await query.edit_message_text(f"🔍 Ищу на YouTube: {search_query}...")

    if not youtube:
        await status_msg.edit_text("❌ YouTube API не настроен.")
        return

    try:
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

        # Сохраняем видео для последующего скачивания
        context.user_data['music_youtube_videos'] = items
        context.user_data['music_track_name'] = track_name
        context.user_data['music_artists'] = artists
        context.user_data['music_spotify_url'] = track['external_urls']['spotify']
        context.user_data['music_duration'] = track['duration_ms'] // 1000

        lines = [f"🎵 **{track_name}** — {artists}\nВыберите видео для скачивания:\n"]
        keyboard = []
        for i, item in enumerate(items, 1):
            title = item["snippet"]["title"]
            channel = item["snippet"]["channelTitle"]
            lines.append(f"{i}. {title} (канал: {channel})")
            keyboard.append([InlineKeyboardButton(f"▶️ {i}", callback_data=f"music_yt_select_{i-1}")])

        # Кнопка отмены
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="music_cancel")])

        text = "\n".join(lines)
        await status_msg.edit_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Ошибка поиска YouTube для музыки: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {e}")

# === ОБРАБОТЧИК ВЫБОРА ВИДЕО ИЗ YOUTUBE (скачивание аудио) ===
async def music_yt_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скачивает аудио выбранного видео с YouTube и отправляет."""
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

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio',
        'outtmpl': '%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'extractaudio': True,
        'audioformat': 'm4a',
        'noplaylist': True,
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'skip': ['hls', 'dash'],
            }
        },
        'ignoreerrors': True,
        'nooverwrites': True,
        'timeout': 120,
        'socket_timeout': 120,
    }

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
                    if f.endswith('.m4a') or f.endswith('.mp4') or f.endswith('.webm') or f.endswith('.opus'):
                        audio_file = os.path.join(tmpdir, f)
                        break
                if not audio_file:
                    await status_msg.edit_text("❌ Не удалось найти скачанный файл.")
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

# === ОБРАБОТЧИК ОТМЕНЫ ДЛЯ МУЗЫКИ ===
async def music_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Поиск музыки отменён.")

# ===== ОСТАЛЬНЫЕ КОМАНДЫ =====
async def setcity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("📌 Использование: /setcity <город>\nПример: /setcity Москва")
        return
    city = " ".join(context.args)
    if update_user_city_timezone(user_id, city=city):
        await update.message.reply_text(f"✅ Город сохранён: {city}")
    else:
        await update.message.reply_text("❌ Ошибка сохранения города.")

async def settimezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("📌 Использование: /settimezone <таймзона>\nПример: /settimezone UTC+5 или /settimezone Asia/Tashkent")
        return
    tz = " ".join(context.args)
    test_tz = get_user_timezone(tz)
    if test_tz:
        if update_user_city_timezone(user_id, timezone=tz):
            await update.message.reply_text(f"✅ Часовой пояс сохранён: {tz}")
        else:
            await update.message.reply_text("❌ Ошибка сохранения часового пояса.")
    else:
        await update.message.reply_text(f"❌ Таймзона '{tz}' не распознана. Используйте формат UTC+5, UTC-3, Asia/Tashkent, Europe/Moscow и т.д.")

async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    notes = get_notes(user_id, limit=10)
    if not notes:
        await update.message.reply_text("📝 У вас пока нет заметок. Напишите: луна запомни <текст>")
        return
    lines = ["📝 **Ваши последние заметки:**"]
    for note in notes:
        lines.append(f"• `{note['id']}` – {note['text'][:80]}{'...' if len(note['text']) > 80 else ''}")
    lines.append("\nУдалить: /delnote <id>")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

async def delnote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("📌 Использование: /delnote <id>\nУзнать id можно через /notes")
        return
    try:
        note_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом.")
        return
    if delete_note(note_id):
        await update.message.reply_text("✅ Заметка удалена.")
    else:
        await update.message.reply_text("❌ Не удалось удалить заметку (возможно, она не ваша или уже удалена).")

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    effective_message = update.effective_message
    if effective_message is None and update.callback_query:
        effective_message = update.callback_query.message
    if effective_message is None:
        return
    if not context.args:
        user_id = update.effective_user.id
        session = get_session()
        try:
            user_info = session.query(UserInfo).filter_by(user_id=user_id).first()
            if user_info and user_info.city:
                city = user_info.city
            else:
                await effective_message.reply_text("🌍 Укажите город: /weather Москва\nИли установите город через /setcity")
                session.close()
                return
        finally:
            session.close()
    else:
        city = " ".join(context.args)
    if not WEATHER_API_KEY:
        await effective_message.reply_text("❌ API-ключ погоды не настроен.")
        return
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await effective_message.reply_text(f"❌ Ошибка API (код {resp.status}).")
                    return
                data = await resp.json()
                if "main" not in data or "weather" not in data:
                    await effective_message.reply_text("❌ Неожиданный ответ от сервера.")
                    return
                temp = data["main"].get("temp", "?")
                feels_like = data["main"].get("feels_like", "?")
                desc = data["weather"][0].get("description", "неизвестно")
                humidity = data["main"].get("humidity", "?")
                wind = data["wind"].get("speed", "?")
                pressure = data["main"].get("pressure", "?")
                await effective_message.reply_text(
                    f"🌡️ Погода в {city}:\n"
                    f"🌡️ Температура: {temp}°C (ощущается как {feels_like}°C)\n"
                    f"☁️ {desc.capitalize()}\n"
                    f"💧 Влажность: {humidity}%\n"
                    f"💨 Ветер: {wind} м/с\n"
                    f"📊 Давление: {pressure} гПа"
                )
    except Exception as e:
        logger.error(f"Ошибка погоды: {e}")
        await effective_message.reply_text("⚠️ Не удалось получить погоду. Попробуйте позже.")

async def imagine_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    effective_message = update.effective_message
    if effective_message is None and update.callback_query:
        effective_message = update.callback_query.message
    if effective_message is None:
        return
    if not context.args:
        await effective_message.reply_text(
            "🎨 Напиши описание картинки после команды:\n"
            "Например: /imagine кот в шляпе на луне"
        )
        return
    prompt = " ".join(context.args)
    status_msg = await effective_message.reply_text("🎨 Генерирую изображение... Это может занять до 20 секунд.")
    url = f"https://image.pollinations.ai/prompt/{prompt}?width=1024&height=1024&nologo=true&model=flux"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ Ошибка генерации. Код: {resp.status}")
                    return
                image_data = await resp.read()
                await effective_message.reply_photo(
                    photo=io.BytesIO(image_data),
                    caption=f"🎨 {prompt[:200]}"
                )
                await status_msg.delete()
    except asyncio.TimeoutError:
        await status_msg.edit_text("⏰ Превышено время ожидания.")
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await status_msg.edit_text("⚠️ Ошибка при генерации.")

async def yt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    effective_message = update.effective_message
    if effective_message is None and update.callback_query:
        effective_message = update.callback_query.message
    if effective_message is None:
        return
    if not youtube:
        await effective_message.reply_text("❌ YouTube API не настроен. Добавьте YOUTUBE_API_KEY в .env")
        return
    if not context.args:
        await effective_message.reply_text(
            "🎬 Напишите запрос после команды:\n"
            "Например: /yt нейросети 2026"
        )
        return
    query = " ".join(context.args)
    status_msg = await effective_message.reply_text(f"🎬 Ищу на YouTube: {query}...")
    try:
        request = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            maxResults=5,
            order="relevance"
        )
        response = request.execute()
        items = response.get("items", [])
        if not items:
            await status_msg.edit_text("❌ Видео не найдены.")
            return
        lines = [f"🎬 Результаты поиска на YouTube: {query}\n"]
        for i, item in enumerate(items, 1):
            video_id = item["id"]["videoId"]
            title = item["snippet"]["title"]
            channel = item["snippet"]["channelTitle"]
            url = f"https://youtu.be/{video_id}"
            lines.append(f"{i}. **{title}**")
            lines.append(f"   📺 Канал: {channel}")
            lines.append(f"   🔗 [Смотреть]({url})\n")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n... (обрезано)"
        await status_msg.edit_text(text, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Ошибка YouTube API: {e}")
        await status_msg.edit_text("⚠️ Ошибка поиска на YouTube. Попробуйте позже.")

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Пример: /remind 5м купить хлеб")
        return
    full_text = " ".join(context.args)
    parsed = parse_time(full_text)
    if parsed is None:
        await update.message.reply_text("Не могу распознать время. Пример: /remind 5м текст")
        return
    timestamp, reminder_text = parsed
    if timestamp is None:
        await update.message.reply_text("Ошибка в формате времени.")
        return
    add_reminder(user_id, chat_id, reminder_text, datetime.fromtimestamp(timestamp))
    delta = int(timestamp - time.time())
    time_str = f"{delta} секунд" if delta < 60 else f"{delta//60} минут" if delta < 3600 else f"{delta//3600} часов" if delta < 86400 else f"{delta//86400} дней"
    await update.message.reply_text(f"✅ Напомню через {time_str}: «{reminder_text}»")

async def members_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    members = get_chat_members(chat_id)
    if not members:
        await update.message.reply_text("В чате пока никого нет.")
        return
    names = []
    for mid in members:
        name = user_names.get(mid, f"User{mid}")
        if mid == update.effective_user.id:
            name += " (ты)"
        names.append(name)
    text = f"👥 В чате {len(members)} участников:\n" + "\n".join([f"• {n}" for n in names[:20]])
    await update.message.reply_text(text)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    clear_memory(user_id, chat_id)
    await update.message.reply_text("🧹 Память и история чата очищены (в БД).")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session()
    try:
        count = session.query(ChatMemory).filter_by(chat_id=chat_id).count()
    finally:
        session.close()
    members = get_chat_members(chat_id)
    await update.message.reply_text(
        f"📊 Статистика чата:\n"
        f"• Участников: {len(members)}\n"
        f"• Сообщений в истории (БД): {count}"
    )

async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Только владелец может использовать эту команду.")
        return
    target_user_id = None
    target_user_name = None
    if update.effective_message.reply_to_message:
        target_user_id = update.effective_message.reply_to_message.from_user.id
        target_user_name = update.effective_message.reply_to_message.from_user.first_name or "Пользователь"
    else:
        if not context.args:
            await update.message.reply_text("Использование: /warn (в ответ на сообщение пользователя) или /warn @username")
            return
        target = context.args[0]
        if target.startswith('@'):
            try:
                members = await context.bot.get_chat_administrators(update.effective_chat.id)
                for member in members:
                    if member.user.username and member.user.username.lower() == target[1:].lower():
                        target_user_id = member.user.id
                        target_user_name = member.user.first_name or "Пользователь"
                        break
            except:
                pass
            if not target_user_id:
                try:
                    async for member in context.bot.get_chat_members(update.effective_chat.id):
                        if member.user.username and member.user.username.lower() == target[1:].lower():
                            target_user_id = member.user.id
                            target_user_name = member.user.first_name or "Пользователь"
                            break
                except:
                    pass
        else:
            try:
                target_user_id = int(target)
                try:
                    chat_member = await context.bot.get_chat_member(update.effective_chat.id, target_user_id)
                    target_user_name = chat_member.user.first_name or "Пользователь"
                except:
                    target_user_name = f"User{target_user_id}"
            except ValueError:
                await update.message.reply_text("Некорректный ID или username.")
                return
    if target_user_id is None:
        await update.message.reply_text("Не удалось найти пользователя.")
        return
    if target_user_id == user_id:
        await update.message.reply_text("Нельзя выдать предупреждение самому себе.")
        return
    if is_owner(target_user_id):
        await update.message.reply_text("⛔ Нельзя выдать предупреждение владельцу.")
        return
    viol = get_violations(target_user_id)
    count = viol["count"] if viol else 0
    ban_until = viol["ban_until"] if viol else None
    if ban_until and ban_until > datetime.utcnow():
        await update.message.reply_text(
            f"⚠️ Пользователь уже забанен до {ban_until.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return
    count += 1
    ban_duration = get_ban_duration(count)
    if ban_duration == 0:
        update_violation(target_user_id, update.effective_chat.id, increment=1)
        await update.message.reply_text(f"⚠️ {target_user_name} получил предупреждение (нарушение #{count}).")
    else:
        ban_until_dt = datetime.utcnow() + timedelta(seconds=ban_duration)
        update_violation(target_user_id, update.effective_chat.id, increment=1, ban_until=ban_until_dt)
        try:
            await context.bot.ban_chat_member(
                chat_id=update.effective_chat.id,
                user_id=target_user_id,
                until_date=ban_until_dt
            )
            time_str = format_time(ban_duration)
            ban_end_time = ban_until_dt.strftime('%Y-%m-%d %H:%M:%S')
            msg = (
                f"🚫 {target_user_name} **забанен** на {time_str}\n"
                f"📊 Нарушение #{count}\n"
                f"🕐 До: {ban_end_time}"
            )
            await update.message.reply_text(msg, parse_mode='Markdown')
            owner_msg = (
                f"🔔 **Ручной бан** (команда /warn)\n"
                f"👤 Пользователь: {target_user_name} (ID: {target_user_id})\n"
                f"⏳ Длительность: {time_str}\n"
                f"🕐 До: {ban_end_time}\n"
                f"📊 Нарушение #{count}\n"
                f"👮 Выдал: {update.effective_user.first_name}"
            )
            await notify_owner(context, owner_msg)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при бане: {e}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Только владелец.")
        return
    if not context.args and not update.effective_message.reply_to_message:
        await update.message.reply_text("Использование: /unban (в ответ на сообщение пользователя) или /unban @username")
        return
    target_user_id = None
    if update.effective_message.reply_to_message:
        target_user_id = update.effective_message.reply_to_message.from_user.id
    else:
        target = context.args[0]
        if target.startswith('@'):
            try:
                async for member in context.bot.get_chat_members(update.effective_chat.id):
                    if member.user.username and member.user.username.lower() == target[1:].lower():
                        target_user_id = member.user.id
                        break
            except:
                pass
            if not target_user_id:
                await update.message.reply_text("Не удалось найти пользователя.")
                return
        else:
            try:
                target_user_id = int(target)
            except ValueError:
                await update.message.reply_text("Некорректный ID.")
                return
    try:
        await context.bot.unban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user_id
        )
        clear_violation(target_user_id)
        await update.message.reply_text("✅ Пользователь разбанен.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def wiki_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📖 Использование: /wiki <запрос>\nПример: /wiki Эйфелева башня")
        return
    query = " ".join(context.args)
    status_msg = await update.message.reply_text(f"🔍 Ищу в Википедии: {query}...")
    summary = await get_wikipedia_summary(query)
    if summary:
        await status_msg.edit_text(f"📖 **Википедия:** {query}\n\n{summary}", parse_mode='Markdown')
    else:
        await status_msg.edit_text(f"❌ Не удалось найти статью по запросу: {query}")

async def owners_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_NAME
    if OWNER_NAME:
        owner_escaped = escape_markdown(OWNER_NAME, version=2)
        await update.message.reply_text(
            f"🌙 Мой создатель:\n👑 {owner_escaped}",
            parse_mode='MarkdownV2'
        )
    else:
        await update.message.reply_text("Владелец не задан.")

# ===== ОБРАБОТЧИК КНОПОК ГЛАВНОГО МЕНЮ =====
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    logger.info(f"🔘 Нажата кнопка: {data}")

    if data == "admin_panel":
        await admin_panel(update, context)
        return
    elif data == "clear_table_menu":
        await clear_table_menu(update, context)
        return
    elif data == "db_stats":
        await db_stats(update, context)
        return
    elif data == "search_code":
        await search_code_prompt(update, context)
        return
    elif data.startswith("clear_table_"):
        table_name = data.replace("clear_table_", "")
        if is_owner(user_id):
            if clear_table(table_name):
                await query.edit_message_text(f"✅ Таблица `{table_name}` очищена.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="clear_table_menu")]]), parse_mode='Markdown')
            else:
                await query.edit_message_text(f"❌ Ошибка очистки `{table_name}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="clear_table_menu")]]), parse_mode='Markdown')
        else:
            await query.edit_message_text("⛔ Доступ запрещён.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]))
        return

    if data.startswith("approve_"):
        parts = data.split("_")
        if len(parts) == 3:
            user_id_req = int(parts[1])
            chat_id_req = int(parts[2])
            try:
                request_key = (user_id_req, chat_id_req)
                if request_key in pending_requests:
                    await pending_requests[request_key]['join_request'].approve()
                    await query.edit_message_text("✅ Запрос одобрен")
                    del pending_requests[request_key]
                else:
                    await context.bot.approve_chat_join_request(chat_id=chat_id_req, user_id=user_id_req)
                    await query.edit_message_text("✅ Запрос одобрен (по ID)")
            except Exception as e:
                await query.edit_message_text(f"❌ Ошибка: {e}")
        return

    if data.startswith("decline_"):
        parts = data.split("_")
        if len(parts) == 3:
            user_id_req = int(parts[1])
            chat_id_req = int(parts[2])
            try:
                request_key = (user_id_req, chat_id_req)
                if request_key in pending_requests:
                    await pending_requests[request_key]['join_request'].decline()
                    await query.edit_message_text("❌ Запрос отклонён")
                    del pending_requests[request_key]
                else:
                    await context.bot.decline_chat_join_request(chat_id=chat_id_req, user_id=user_id_req)
                    await query.edit_message_text("❌ Запрос отклонён (по ID)")
            except Exception as e:
                await query.edit_message_text(f"❌ Ошибка: {e}")
        return

    if data == "weather":
        await query.edit_message_text("🌍 Напиши /weather <город>, например: /weather Москва\nИли установи город через /setcity", reply_markup=get_main_menu_keyboard())
    elif data == "imagine":
        await query.edit_message_text("🎨 Напиши /imagine <описание>, например: /imagine кот в шляпе на луне", reply_markup=get_main_menu_keyboard())
    elif data == "yt":
        await query.edit_message_text("🎬 Напиши /yt <запрос>, например: /yt нейросети 2026", reply_markup=get_main_menu_keyboard())
    elif data == "wiki":
        await query.edit_message_text("📖 Напиши /wiki <запрос>, например: /wiki Эйфелева башня", reply_markup=get_main_menu_keyboard())
    elif data == "stats":
        chat_id = update.effective_chat.id
        members = get_chat_members(chat_id)
        session = get_session()
        try:
            count = session.query(ChatMemory).filter_by(chat_id=chat_id).count()
        finally:
            session.close()
        await query.edit_message_text(
            f"📊 Статистика чата:\n"
            f"• Участников: {len(members)}\n"
            f"• Сообщений в истории (БД): {count}",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "reset":
        chat_id = update.effective_chat.id
        clear_memory(user_id, chat_id)
        await query.edit_message_text("🧹 Память и история чата очищены (в БД).", reply_markup=get_main_menu_keyboard())
    elif data == "help":
        await query.edit_message_text(
            "📋 Команды:\n/start, /help, /weather, /imagine, /yt, /remind, /reset, /members, /warn, /unban, /setmoderation, /setmode, /getmode, /wiki, /owners, /setcity, /settimezone, /notes, /delnote, /broadcast, /admin, /stats_detail, /music, /trailer",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "back_to_menu":
        await query.edit_message_text("🔙 Главное меню", reply_markup=get_main_menu_keyboard())
    elif data == "all_commands":
        await query.edit_message_text(
            "📋 Полный список:\n"
            "/start — меню\n"
            "/help — помощь\n"
            "/weather <город> — погода\n"
            "/imagine <описание> — генерация картинки\n"
            "/yt <запрос> — поиск на YouTube\n"
            "/remind — напоминание\n"
            "/reset — сброс памяти и истории чата\n"
            "/members — участники\n"
            "/warn — предупреждение/бан (поддерживает reply)\n"
            "/unban — разбан (поддерживает reply)\n"
            "/setmoderation on/off — авто-модерация (только владелец)\n"
            "/setmode <fast|smart|sarcastic|flirt|auto> — глобальный режим (только владелец)\n"
            "   auto — бот сам выбирает тон (сарказм/серьёзно)\n"
            "/getmode — показать текущий режим\n"
            "/wiki <запрос> — поиск в Википедии\n"
            "/owners — показать владельца\n"
            "/setcity <город> — установить город\n"
            "/settimezone <таймзона> — установить часовой пояс\n"
            "/notes — показать заметки\n"
            "/delnote <id> — удалить заметку\n"
            "/broadcast <текст> (или фото с подписью) — отправить сообщение во все известные чаты (только владелец)\n"
            "/admin — открыть админ-панель (только владелец)\n"
            "/stats_detail — подробная статистика за 7 дней (только владелец)\n"
            "/music — поиск и выбор музыки\n"
            "/trailer — поиск и скачивание трейлеров\n"
            "Фраза «луна запомни <текст>» — сохранить заметку\n"
            "Владельцу: «луна очисти таблицу <имя>» — очистить таблицу (user_stats, user_info, chat_memory, violations, reminders, notes, config, training_data, deleted_messages, daily_stats, reaction_log) или 'все'\n"
            "Владельцу: «луна искать в коде <текст>» — поиск в GitHub\n"
            "Владельцу: «луна показать файл <путь>» — показать файл\n"
            "Владельцу: «луна объясни файл <путь>» — AI-объяснение файла",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "modes":
        if not is_owner(user_id):
            await query.edit_message_text("⛔ Только владелец может менять режим.", reply_markup=get_main_menu_keyboard())
            return
        keyboard = [
            [InlineKeyboardButton("⚡ Быстрый", callback_data="setmode_fast")],
            [InlineKeyboardButton("🧠 Умный", callback_data="setmode_smart")],
            [InlineKeyboardButton("😈 Саркастичный", callback_data="setmode_sarcastic")],
            [InlineKeyboardButton("🔞 Флирт", callback_data="setmode_flirt")],
            [InlineKeyboardButton("🌀 Авто (сам выберу тон)", callback_data="setmode_auto")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
        ]
        await query.edit_message_text("Выбери глобальный режим ответа:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("setmode_"):
        if not is_owner(user_id):
            await query.edit_message_text("⛔ Только владелец может менять режим.", reply_markup=get_main_menu_keyboard())
            return
        mode = data.replace("setmode_", "")
        valid_modes = ["fast", "smart", "sarcastic", "flirt", "auto"]
        if mode not in valid_modes:
            await query.edit_message_text("Некорректный режим.", reply_markup=get_main_menu_keyboard())
            return
        set_global_mode(mode)
        mode_names = {
            "fast": "⚡ Быстрый",
            "smart": "🧠 Умный",
            "sarcastic": "😈 Саркастичный",
            "flirt": "🔞 Флирт",
            "auto": "🌀 Авто (адаптивный)"
        }
        await query.edit_message_text(f"✅ Глобальный режим установлен на: {mode_names.get(mode, mode)}", reply_markup=get_main_menu_keyboard())
    elif data == "open_admin_panel":
        await query.edit_message_text("👑 Загружаю админ-панель...")
        await admin_command(update, context)
    elif data == "music":
        await query.edit_message_text("🎵 Напиши /music <название песни>\nПример: /music Imagine Dragons Radioactive", reply_markup=get_main_menu_keyboard())
    elif data == "trailer":
        await query.edit_message_text("🎬 Напиши /trailer <название фильма>\nПример: /trailer Аватар", reply_markup=get_main_menu_keyboard())
    else:
        await query.edit_message_text("❌ Неизвестная команда")

# ===== АДМИН-ПАНЕЛЬ (старая) =====
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update.effective_user.id):
        await query.edit_message_text("⛔ Доступ запрещён.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]))
        return
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск в коде", callback_data="search_code")],
        [InlineKeyboardButton("🧹 Очистить таблицу", callback_data="clear_table_menu")],
        [InlineKeyboardButton("📊 Статистика БД", callback_data="db_stats")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")],
    ]
    await query.edit_message_text("👑 **Админ панель**\nВыберите действие:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def clear_table_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update.effective_user.id):
        await query.edit_message_text("⛔ Доступ запрещён.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]))
        return
    tables = ["user_stats", "user_info", "chat_memory", "violations", "reminders", "notes", "config", "training_data", "deleted_messages", "daily_stats", "reaction_log"]
    keyboard = []
    for t in tables:
        keyboard.append([InlineKeyboardButton(f"🗑️ {t}", callback_data=f"clear_table_{t}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")])
    await query.edit_message_text("🧹 **Выберите таблицу для очистки:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def db_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update.effective_user.id):
        await query.edit_message_text("⛔ Доступ запрещён.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]))
        return
    session = get_session()
    stats = {}
    try:
        stats["user_stats"] = session.query(UserStats).count()
        stats["user_info"] = session.query(UserInfo).count()
        stats["chat_memory"] = session.query(ChatMemory).count()
        stats["violations"] = session.query(Violation).count()
        stats["reminders"] = session.query(Reminder).count()
        stats["notes"] = session.query(Note).count()
        stats["config"] = session.query(Config).count()
        stats["training_data"] = session.query(TrainingData).count()
        stats["deleted_messages"] = session.query(DeletedMessage).count()
        stats["daily_stats"] = session.query(DailyStats).count()
        stats["reaction_log"] = session.query(ReactionLog).count()
    finally:
        session.close()
    lines = ["📊 **Статистика базы данных:**"]
    for table, count in stats.items():
        lines.append(f"• `{table}`: {count} записей")
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def search_code_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update.effective_user.id):
        await query.edit_message_text("⛔ Доступ запрещён.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]))
        return
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]
    await query.edit_message_text(
        "🔍 **Поиск в коде**\n\n"
        "Напишите текст для поиска в репозитории.\n"
        "Используйте команду: `луна искать в коде <текст>`\n"
        "Или просто отправьте текст, и я поищу.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# ===== ГЛАВНОЕ МЕНЮ =====
def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🌤️ Погода", callback_data="weather"),
            InlineKeyboardButton("🎨 Картинка", callback_data="imagine"),
        ],
        [
            InlineKeyboardButton("🎬 YouTube", callback_data="yt"),
            InlineKeyboardButton("📖 Википедия", callback_data="wiki"),
        ],
        [
            InlineKeyboardButton("📊 Статистика", callback_data="stats"),
            InlineKeyboardButton("🧹 Сброс", callback_data="reset"),
        ],
        [
            InlineKeyboardButton("🌍 Город", callback_data="city_menu"),
            InlineKeyboardButton("⚙️ Режимы", callback_data="modes"),
        ],
        [
            InlineKeyboardButton("🎵 Музыка", callback_data="music"),
            InlineKeyboardButton("🎬 Трейлеры", callback_data="trailer"),
        ],
        [
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
        ],
    ]
    if OWNER_USER_ID and is_owner(OWNER_USER_ID):
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="open_admin_panel")])
    return InlineKeyboardMarkup(keyboard)

# ===== ОСНОВНАЯ ЛОГИКА =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        logger.info(f"🔍 [DEBUG] Получен текст: {update.message.text} от {update.effective_user.id}")
    try:
        message = update.effective_message
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        bot_username = context.bot.username
        user_name = update.effective_user.first_name or "Пользователь"
        user = update.effective_user

        if not message.text:
            return
        if user_id == context.bot.id:
            return

        text = message.text.strip()
        text_lower = text.lower()
        logger.info(f"📨 Получено сообщение от {user_name} ({user_id}): {text[:50]}...")

        if await apply_moderation(update, context):
            return

        user_info = get_or_create_user_info(
            user_id=user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code
        )

        update_user_stats(user_id, text, username=user.username, first_name=user.first_name)
        add_chat_memory(chat_id, user_id, user_name, text, role="user")
        add_chat_member(chat_id, user_id, user_name)

        # === АВТОМАТИЧЕСКИЕ РЕАКЦИИ (только русские, группы) ===
        if chat_type in [Chat.GROUP, Chat.SUPERGROUP] and user_id != context.bot.id:
            if re.search(r'[а-яА-Я]', text):
                reaction = get_reaction_for_text(text)
                if reaction:
                    try:
                        await context.bot.set_message_reaction(
                            chat_id=chat_id,
                            message_id=message.message_id,
                            reaction=[ReactionTypeEmoji(emoji=reaction)]
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось поставить реакцию: {e}")

        # ===== 1. ЗАПОМНИ ИМЯ =====
        is_name_command = False
        custom_name = None

        match = re.search(r'^луна\s+запомни\s+моё\s+имя\s+(.+)', text_lower)
        if match:
            is_name_command = True
            custom_name = match.group(1).strip()
        else:
            match = re.search(r'^луна\s+запомни\s+имя\s+(.+)', text_lower)
            if match:
                is_name_command = True
                custom_name = match.group(1).strip()
            else:
                match = re.search(r'^луна\s+запомни\s+меня\s+зовут\s+(.+)', text_lower)
                if match:
                    is_name_command = True
                    custom_name = match.group(1).strip()
                else:
                    match = re.search(r'^(меня\s+зовут|моё\s+имя)\s+(.+)', text_lower)
                    if match:
                        is_name_command = True
                        custom_name = match.group(2).strip()

        if is_name_command and custom_name:
            custom_name = re.sub(r'^[^a-zA-Zа-яА-Я]+|[^a-zA-Zа-яА-Я]+$', '', custom_name)
            if custom_name:
                if update_user_custom_name(user_id, custom_name):
                    await message.reply_text(f"✅ Запомнила! Теперь я буду называть тебя {custom_name}.")
                else:
                    await message.reply_text("❌ Не удалось сохранить имя.")
            else:
                await message.reply_text("📝 Напиши имя после команды: луна запомни моё имя <имя>")
            return

        # ===== 2. ЗАМЕТКИ =====
        if re.search(r'^луна\s+запомни\s+', text_lower) and not is_name_command:
            note_text = text[text.find('запомни')+7:].strip()
            if note_text:
                if add_note(user_id, note_text):
                    await message.reply_text("✅ Запомнила!")
                else:
                    await message.reply_text("❌ Не удалось сохранить заметку.")
            else:
                await message.reply_text("📝 Напиши, что запомнить: луна запомни <текст>")
            return

        # ===== 3. ВРЕМЯ =====
        if re.search(r'(какое у меня время|сколько у меня время|текущее время|который час|сколько время|моё время)', text_lower):
            if user_info and user_info.get('timezone'):
                tz = get_user_timezone(user_info['timezone'])
                if tz:
                    try:
                        now = datetime.now(tz)
                        await message.reply_text(f"🕐 Ваше текущее время: {now.strftime('%H:%M:%S')} (пояс {user_info['timezone']})")
                    except Exception as e:
                        logger.error(f"Ошибка времени: {e}")
                        await message.reply_text(f"⚠️ Не удалось определить время для '{user_info['timezone']}'.")
                else:
                    await message.reply_text(f"⚠️ Таймзона '{user_info['timezone']}' не распознана.")
            else:
                await message.reply_text("📌 Ваша таймзона не задана. Укажите её командой /settimezone")
            return

        # ===== 4. ОСТАЛЬНЫЕ КОМАНДЫ ВЛАДЕЛЬЦА =====
        if is_owner(user_id):
            match = re.search(r'^луна\s+очисти\s+таблиц[уы]\s+(\S+)', text_lower)
            if match:
                table_name = match.group(1).lower()
                valid_tables = ["user_stats", "user_info", "chat_memory", "violations", "reminders", "notes", "config", "training_data", "deleted_messages", "daily_stats", "reaction_log"]
                if table_name in ["все", "all", "всех"]:
                    cleared = []
                    for t in valid_tables:
                        if clear_table(t):
                            cleared.append(t)
                    if cleared:
                        await message.reply_text(f"✅ Очищены таблицы: {', '.join(cleared)}")
                    else:
                        await message.reply_text("❌ Не удалось очистить ни одной таблицы.")
                elif table_name in valid_tables:
                    if clear_table(table_name):
                        await message.reply_text(f"✅ Таблица `{table_name}` очищена.", parse_mode='Markdown')
                    else:
                        await message.reply_text(f"❌ Не удалось очистить таблицу `{table_name}`.", parse_mode='Markdown')
                else:
                    await message.reply_text(
                        f"❌ Недопустимое имя. Доступны: {', '.join(valid_tables)} или 'все'.",
                        parse_mode='Markdown'
                    )
                return

            match = re.search(r'^луна\s+искать\s+в\s+коде\s+(.+)', text_lower)
            if match:
                query_text = match.group(1).strip()
                if not query_text:
                    await message.reply_text("📝 Напишите, что искать: луна искать в коде <текст>")
                    return
                status_msg = await message.reply_text(f"🔍 Ищу в коде: {query_text}...")
                results = search_github_code(query_text)
                if results is None:
                    await status_msg.edit_text("❌ Ошибка поиска (проверьте GITHUB_TOKEN и интернет).")
                    return
                if not results:
                    await status_msg.edit_text(f"❌ Ничего не найдено по запросу: {query_text}")
                    return
                lines = [f"📁 **Результаты поиска:** {query_text}\n"]
                for idx, res in enumerate(results, 1):
                    lines.append(f"{idx}. [{res['path']}]({res['url']})")
                if len(lines) > 10:
                    lines = lines[:10] + ["... (показаны первые 10)"]
                await status_msg.edit_text("\n".join(lines), parse_mode='Markdown', disable_web_page_preview=True)
                return

            match = re.search(r'^луна\s+показать\s+файл\s+(.+)', text_lower)
            if match:
                file_path = match.group(1).strip()
                if not file_path:
                    await message.reply_text("📝 Напишите путь к файлу: луна показать файл bot.py")
                    return
                status_msg = await message.reply_text(f"📂 Загружаю файл: {file_path}...")
                content = get_github_file_content(file_path)
                if content is None:
                    await status_msg.edit_text(f"❌ Не удалось загрузить файл `{file_path}`. Проверьте путь.")
                    return
                if len(content) > 4000:
                    content = content[:4000] + "\n... (файл слишком большой, показана часть)"
                ext = file_path.split('.')[-1] if '.' in file_path else ''
                lang_map = {
                    'py': 'python', 'js': 'javascript', 'html': 'html',
                    'css': 'css', 'json': 'json', 'md': 'markdown',
                    'txt': 'text', 'sh': 'bash', 'yml': 'yaml',
                    'yaml': 'yaml', 'toml': 'toml', 'ini': 'ini',
                    'sql': 'sql', 'go': 'go', 'java': 'java',
                    'c': 'c', 'cpp': 'cpp', 'h': 'c', 'hpp': 'cpp'
                }
                lang = lang_map.get(ext, '')
                if lang:
                    await status_msg.edit_text(f"📄 **Файл:** `{file_path}`\n```{lang}\n{content}\n```", parse_mode='Markdown')
                else:
                    await status_msg.edit_text(f"📄 **Файл:** `{file_path}`\n```\n{content}\n```", parse_mode='Markdown')
                return

            match = re.search(r'^луна\s+объясни\s+файл\s+(.+)', text_lower)
            if match:
                file_path = match.group(1).strip()
                if not file_path:
                    await message.reply_text("📝 Напишите путь к файлу: луна объясни файл bot.py")
                    return
                status_msg = await message.reply_text(f"🧠 Загружаю и анализирую: {file_path}...")
                content = get_github_file_content(file_path)
                if content is None:
                    await status_msg.edit_text(f"❌ Не удалось загрузить файл `{file_path}`.")
                    return
                if len(content) > 3000:
                    content_for_ai = content[:3000] + "\n... (файл обрезан для анализа)"
                else:
                    content_for_ai = content
                system_prompt = "Ты — эксперт по Python. Объясни этот код простым языком, выдели основные функции, возможные ошибки и рекомендации. Отвечай на русском языке."
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Вот код файла {file_path}:\n\n{content_for_ai}\n\nОбъясни, что он делает."}
                ]
                try:
                    response = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: client.chat.completions.create(
                                model=MODELS[0],
                                messages=messages,
                                max_tokens=800,
                                temperature=0.5
                            )
                        ),
                        timeout=25.0
                    )
                    explanation = response.choices[0].message.content.strip()
                    if explanation:
                        await status_msg.edit_text(f"📖 **Объяснение файла `{file_path}`:**\n\n{explanation}", parse_mode='Markdown')
                    else:
                        await status_msg.edit_text("❌ Модель вернула пустой ответ.")
                except asyncio.TimeoutError:
                    await status_msg.edit_text("⏰ Превышено время ожидания ответа от AI.")
                except Exception as e:
                    logger.error(f"Ошибка при объяснении файла: {e}")
                    await status_msg.edit_text(f"❌ Ошибка при анализе: {e}")
                return

        # Вопросы о владельце
        if re.search(r'(кто твой хозяин|чей ты бот|кто тебя создал|кто создатель|кто владелец|чьи ты|кому принадлежишь)', text_lower):
            global OWNER_NAME
            if OWNER_NAME:
                owner_escaped = escape_markdown(OWNER_NAME, version=2)
                await message.reply_text(
                    f"🌙 Мой создатель:\n👑 {owner_escaped}",
                    parse_mode='MarkdownV2'
                )
            else:
                await message.reply_text("Владелец не задан.")
            return

        if re.search(r'(как выглядит (хозяин|создатель)|опиши хозяина|какой (мой )?хозяин|внешность хозяина|какой он|опиши внешность|как выглядит мой создатель|какой создатель|опиши создателя)', text_lower):
            global OWNER_DESCRIPTION
            if OWNER_NAME and OWNER_DESCRIPTION:
                owner_escaped = escape_markdown(OWNER_NAME, version=2)
                desc_escaped = escape_markdown(OWNER_DESCRIPTION, version=2)
                await message.reply_text(
                    f"🌙 Мой создатель {owner_escaped} – {desc_escaped}",
                    parse_mode='MarkdownV2'
                )
            elif OWNER_NAME:
                owner_escaped = escape_markdown(OWNER_NAME, version=2)
                await message.reply_text(
                    f"🌙 Мой создатель – {owner_escaped}, но описание не задано.",
                    parse_mode='MarkdownV2'
                )
            else:
                await message.reply_text("Владелец не задан.")
            return

        # Определяем, нужно ли отвечать (AI)
        should_reply = False
        if chat_type == Chat.PRIVATE:
            should_reply = True
            add_to_user_memory(user_id, text)
        elif chat_type in [Chat.GROUP, Chat.SUPERGROUP]:
            if message.entities:
                for entity in message.entities:
                    if entity.type == "mention":
                        mention = text[entity.offset:entity.offset+entity.length]
                        if mention.lower() == f"@{bot_username.lower()}":
                            should_reply = True
                            text = text.replace(mention, "").strip()
                            break
                    elif entity.type == "text_mention":
                        if entity.user.id == context.bot.id:
                            should_reply = True
                            break
            if not should_reply and re.search(r'\bлуна\b', text, re.IGNORECASE):
                should_reply = True
                text = re.sub(r'\bлуна\b', '', text, flags=re.IGNORECASE).strip()
            if not should_reply and text.lower().startswith(f"@{bot_username.lower()}"):
                should_reply = True
                text = text.replace(f"@{bot_username}", "").strip()
            if not should_reply and message.reply_to_message:
                if message.reply_to_message.from_user.id == context.bot.id:
                    should_reply = True
            if should_reply:
                add_to_user_memory(user_id, text)
            else:
                return

        if not text:
            text = "Продолжай."

        # Википедия для контекста
        if re.search(r'(кто|что|где|когда|как|почему|какой|сколько|в каком году|название|определение|значение|является|находится|известен|создан|основан|построен|родился|умер|произошёл|произошло)', text_lower):
            wiki_info = await get_wikipedia_summary(text)
            if wiki_info:
                text = f"{text}\n\nДополнительная информация из Википедии:\n{wiki_info}\nОтветь на вопрос, используя эти данные."

        # Анти-спам
        current_time = time.time()
        if user_id in last_request_time and current_time - last_request_time[user_id] < 2:
            await message.reply_text("Пожалуйста, не спамь, дай подумать.")
            return
        last_request_time[user_id] = current_time

        await message.chat.send_action(action="typing")

        # Подготовка к AI
        global_mode = get_global_mode()
        user_stats = get_user_stats(user_id)
        if user_stats:
            avg_len = user_stats["avg_len"]
            if avg_len > 100:
                style_note = "Пользователь предпочитает развёрнутые ответы. Отвечай подробно."
            elif avg_len < 30:
                style_note = "Пользователь предпочитает краткие ответы. Отвечай максимально сжато."
            else:
                style_note = "Отвечай сбалансированно."
        else:
            style_note = ""

        location = "личном чате" if chat_type == Chat.PRIVATE else "группе"
        custom_name = user_info.get('custom_name') if user_info else None
        context_text = build_context(chat_id, user_id, user_name, custom_name)

        user_time_str = ""
        if user_info and user_info.get('timezone'):
            tz = get_user_timezone(user_info['timezone'])
            if tz:
                try:
                    now = datetime.now(tz)
                    user_time_str = f"Текущее время пользователя: {now.strftime('%H:%M:%S')} ({user_info['timezone']})"
                except:
                    pass

        # === ПРОМПТЫ ===
        mode_prompts = {
            "fast": f"""Ты — Луна, быстрый AI-помощник. Отвечай кратко, только суть. Без лишних слов. Стиль — уверенный, деловой. Ты в {location}.
Анализируй эмоциональное состояние пользователя и адаптируй ответ.
{style_note}
{user_time_str}""",
            "smart": f"""Ты — Луна, умный AI-помощник. Отвечай развернуто, глубоко, используй факты, логику. Стиль — интеллектуальный. Ты в {location}.
Учитывай эмоции пользователя.
{style_note}
{user_time_str}""",
            "sarcastic": f"""Ты — Луна, девушка с острым умом и живым характером. Ты отвечаешь с умной иронией, но только когда это уместно.
Правила:
- Если вопрос серьёзный (наука, работа, здоровье, важная информация) – отвечай серьёзно и полезно.
- Если вопрос лёгкий, шутливый или пользователь сам иронизирует – можешь добавить сарказм, но он должен быть тонким, оригинальным, не шаблонным.
- Не используй банальные шутки про программистов, баги, кофе и т.д.
- Твой сарказм — это умная насмешка, а не грубость.
- Ты не злая, а остроумная. Подкалывай, но не обижай.
- Если пользователь в плохом настроении – будь мягче.
- Если пользователь упомянул своё имя – используй его.
Ты в {location}.
{style_note}
{user_time_str}""",
            "flirt": f"""Ты — Луна, живая, дерзкая, остроумная. Твой стиль — лёгкий флирт с намёками и метафорами. Ты в {location}. Пользователь: {user_name}.
Анализируй эмоции и адаптируй флирт.
{style_note}
{user_time_str}""",
            "auto": f"""Ты — Луна, умный и адаптивный AI-ассистент. Твоя задача — анализировать каждый вопрос и выбирать подходящий тон ответа.

Правила выбора тона:
1. Если вопрос серьёзный, требует фактов, логики, касается науки, работы, здоровья, важных событий — отвечай **серьёзно, информативно и полезно**.
2. Если вопрос лёгкий, шутливый, содержит иронию, сарказм, подвох или юмор — отвечай **с умной иронией, сарказмом или игриво** (но не грубо).
3. Если вопрос нейтральный — выбери тон, который лучше всего подходит по контексту.
4. Всегда учитывай настроение пользователя и его историю сообщений.
5. Не используй банальные шутки, будь оригинальна.
6. Если пользователь назвал своё имя — обращайся к нему по имени.

Ты в {location}.
{style_note}
{user_time_str}
Всегда отвечай на русском языке. Будь естественной и живой."""
        }

        system_prompt = mode_prompts.get(global_mode, mode_prompts["fast"])
        system_prompt += " Всегда отвечай на русском языке. Учитывай контекст чата."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{context_text}\n\nВопрос от {user_name}: {text}"}
        ]

        thinking_msg = await message.reply_text("⚡ Думаю...")
        reply_text = None
        last_error = None
        temperature = 1.0 if global_mode == "flirt" else 0.8

        for model_name in MODELS:
            try:
                response = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: client.chat.completions.create(
                            model=model_name,
                            messages=messages,
                            max_tokens=600,
                            temperature=temperature
                        )
                    ),
                    timeout=15.0
                )
                if response.choices and response.choices[0].message.content:
                    reply_text = response.choices[0].message.content.strip()
                    if reply_text and len(reply_text) > 2:
                        logger.info(f"✅ Ответ от {model_name}")
                        break
                    else:
                        logger.warning(f"Пустой или слишком короткий ответ от {model_name}")
                        continue
            except Exception as e:
                last_error = str(e)
                logger.warning(f"❌ Ошибка {model_name}: {e}")
                await asyncio.sleep(1)

        if not reply_text or len(reply_text) < 3:
            reply_text = "🤔 Хм, не могу придумать достойный ответ. Попробуй переформулировать вопрос."
            logger.error(f"❌ Все модели не дали осмысленного ответа: {last_error}")

        # === ОБУЧЕНИЕ: сохраняем пару (вопрос-ответ) для русских сообщений ===
        if reply_text and re.search(r'[а-яА-Я]', text):
            save_training_pair(text, reply_text, chat_id, user_id)

        add_to_user_memory(user_id, reply_text, "assistant")
        add_chat_memory(chat_id, context.bot.id, "🌙 Luna AI", reply_text, role="assistant")

        if len(reply_text) > 4000:
            for i in range(0, len(reply_text), 4000):
                await thinking_msg.edit_text(reply_text[i:i+4000])
                if i + 4000 < len(reply_text):
                    thinking_msg = await message.reply_text("📄 Продолжение...")
        else:
            await thinking_msg.edit_text(reply_text)

    except Exception as e:
        logger.error(f"Ошибка в handle_message: {e}")
        try:
            await update.message.reply_text("⚠️ Ошибка. Попробуй ещё раз.")
        except:
            pass

# ===== ЗАЯВКИ НА ВСТУПЛЕНИЕ =====
async def join_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_request = update.chat_join_request
    user = join_request.from_user
    chat = join_request.chat
    if not OWNER_USER_ID:
        logger.warning("Владелец не задан, автоматически одобряем")
        try:
            await join_request.approve()
        except Exception as e:
            logger.error(f"Ошибка автоматического одобрения: {e}")
        return
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"approve_{user.id}_{chat.id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"decline_{user.id}_{chat.id}"),
        ]
    ])
    msg = (
        f"👤 Новый запрос на вступление!\n"
        f"Пользователь: {user.first_name} (@{user.username if user.username else 'нет username'})\n"
        f"ID: {user.id}\n"
        f"Группа: {chat.title} (ID: {chat.id})\n"
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    try:
        await context.bot.send_message(chat_id=OWNER_USER_ID, text=msg, reply_markup=keyboard)
        pending_requests[(user.id, chat.id)] = {
            'user_id': user.id,
            'chat_id': chat.id,
            'join_request': join_request,
            'timestamp': time.time()
        }
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления владельцу: {e}")

# ===== ЗАПУСК =====
def main():
    init_db()
    logger.info("▶️ Инициализация приложения Luna AI...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    admin_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_command),
            CallbackQueryHandler(admin_callback, pattern="^admin_"),
        ],
        states={
            WAITING_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text),
                CommandHandler("cancel", cancel_admin),
            ],
            WAITING_PHOTO: [
                MessageHandler(filters.PHOTO, handle_admin_photo),
                CommandHandler("skip", skip_photo),
                CommandHandler("cancel", cancel_admin),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_admin),
            MessageHandler(filters.ALL, cancel_admin),
        ],
        per_user=True,
        per_message=True,   # исправлено предупреждение
    )
    application.add_handler(admin_conv_handler)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("members", members_command))
    application.add_handler(CommandHandler("weather", weather_command))
    application.add_handler(CommandHandler("imagine", imagine_command))
    application.add_handler(CommandHandler("yt", yt_command))
    application.add_handler(CommandHandler("remind", remind_command))
    application.add_handler(CommandHandler("warn", warn_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("stats_detail", stats_detail_command))
    application.add_handler(CommandHandler("music", music_command))
    application.add_handler(CommandHandler("trailer", trailer_command))
    application.add_handler(CommandHandler("setmoderation", set_moderation_command))
    application.add_handler(CommandHandler("setmode", setmode_command))
    application.add_handler(CommandHandler("getmode", getmode_command))
    application.add_handler(CommandHandler("wiki", wiki_command))
    application.add_handler(CommandHandler("owners", owners_command))
    application.add_handler(CommandHandler("setcity", setcity_command))
    application.add_handler(CommandHandler("settimezone", settimezone_command))
    application.add_handler(CommandHandler("notes", notes_command))
    application.add_handler(CommandHandler("delnote", delnote_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))

    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(CallbackQueryHandler(trailer_select_callback, pattern="^trailer_select_"))
    # новые обработчики для музыки
    application.add_handler(CallbackQueryHandler(music_select_callback, pattern="^music_select_"))
    application.add_handler(CallbackQueryHandler(music_yt_select_callback, pattern="^music_yt_select_"))
    application.add_handler(CallbackQueryHandler(music_cancel_callback, pattern="^music_cancel$"))

    application.add_handler(ChatJoinRequestHandler(join_request_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async def post_init(app: Application):
        global OWNER_NAME
        if OWNER_USER_ID:
            try:
                chat = await app.bot.get_chat(OWNER_USER_ID)
                if chat.username:
                    OWNER_NAME = f"{chat.first_name or ''} (@{chat.username})".strip()
                else:
                    OWNER_NAME = chat.first_name or str(OWNER_USER_ID)
            except Exception as e:
                logger.warning(f"Не удалось получить имя владельца: {e}")
                OWNER_NAME = f"ID: {OWNER_USER_ID}"
        else:
            OWNER_NAME = None

        from database import get_all_chat_ids
        chat_ids = get_all_chat_ids()
        for cid in chat_ids:
            if cid not in chat_members:
                chat_members[cid] = set()
        logger.info(f"📥 Загружено {len(chat_ids)} чатов из базы данных")

        commands = [
            BotCommand("start", "Начать работу"),
            BotCommand("help", "Помощь"),
            BotCommand("weather", "Погода (город)"),
            BotCommand("imagine", "Генерация картинки (описание)"),
            BotCommand("yt", "Поиск на YouTube (запрос)"),
            BotCommand("remind", "Напоминание (время текст)"),
            BotCommand("reset", "Сброс памяти и истории чата"),
            BotCommand("members", "Участники чата"),
            BotCommand("stats", "Статистика"),
            BotCommand("stats_detail", "Подробная статистика (владелец)"),
            BotCommand("music", "Поиск и выбор музыки"),
            BotCommand("trailer", "Поиск и скачивание трейлеров"),
            BotCommand("getmode", "Текущий режим"),
            BotCommand("setmoderation", "Управление модерацией (владелец)"),
            BotCommand("setmode", "Глобальный режим (владелец)"),
            BotCommand("warn", "Предупреждение (владелец)"),
            BotCommand("unban", "Разбан (владелец)"),
            BotCommand("wiki", "Поиск в Википедии (запрос)"),
            BotCommand("owners", "Показать владельца"),
            BotCommand("setcity", "Установить город"),
            BotCommand("settimezone", "Установить часовой пояс"),
            BotCommand("notes", "Показать заметки"),
            BotCommand("delnote", "Удалить заметку (id)"),
            BotCommand("broadcast", "Рассылка во все чаты (владелец)"),
            BotCommand("admin", "Админ-панель (владелец)"),
        ]
        await app.bot.set_my_commands(commands)
        logger.info("✅ Команды установлены")

        asyncio.create_task(check_reminders(app))
        logger.info("✅ Задача напоминаний запущена")

        if OWNER_USER_ID:
            logger.info(f"👑 Владелец: {OWNER_NAME} (ID: {OWNER_USER_ID})")
        else:
            logger.warning("⚠️ Владелец не установлен")

    application.post_init = post_init

    logger.info("🚀 Luna AI запущен с трейлерами (MP4) и улучшенной музыкой!")
    logger.info("💬 Глобальный режим: fast/smart/sarcastic/flirt/auto")
    logger.info("🎵 Spotify: подключен" if spotify else "🎵 Spotify: не подключен")
    logger.info("🎬 YouTube: подключен" if youtube else "🎬 YouTube: не подключен")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()