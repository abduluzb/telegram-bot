# bot.py - Luna AI с многоуровневым меню и GitHub-поиском

import os
import asyncio
import logging
import time
import re
import aiohttp
import io
import requests
from typing import Dict, List, Set, Tuple, Optional
from datetime import datetime, timedelta
import pytz

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from dotenv import load_dotenv
from telegram import Update, Chat, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    filters,
    ContextTypes,
)
from telegram.helpers import escape_markdown
from cerebras.cloud.sdk import Cerebras
from googleapiclient.discovery import build

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
    add_note,
    get_notes,
    delete_note,
    clear_table,
    update_user_interests,
    get_user_interests,
    get_user_history,
    get_session,
    UserStats,
    UserInfo,
    ChatMemory,
    Violation,
    Reminder,
    Note,
    Config,
    UserInterest,
)

# ============== НАСТРОЙКИ ==============
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не найден в .env файле!")
if not CEREBRAS_API_KEY:
    raise ValueError("❌ CEREBRAS_API_KEY не найден в .env файле!")

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

# ============== ПОДКЛЮЧЕНИЕ К CEREBRAS ==============
client = Cerebras(api_key=CEREBRAS_API_KEY)
MODELS = ["gpt-oss-120b", "zai-glm-4.7"]
logger.info(f"✅ Cerebras API настроен. Моделей: {len(MODELS)}")

# ============== ПОДКЛЮЧЕНИЕ К YOUTUBE ==============
youtube = None
if YOUTUBE_API_KEY:
    try:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        logger.info("✅ YouTube API подключен")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения YouTube API: {e}")
else:
    logger.warning("⚠️ YOUTUBE_API_KEY не задан, команда /yt будет недоступна")

# ============== ХРАНИЛИЩА ==============
chat_members: Dict[int, Set[int]] = {}
user_names: Dict[int, str] = {}
last_request_time: Dict[int, float] = {}
user_memory: Dict[int, List[Dict]] = {}
MAX_MEMORY = 50

# ============== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ВРЕМЕНИ ==============
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

# ============== ПОИСК В GITHUB ==============
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
            logger.error(f"GitHub API error: {response.status_code}")
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

# ============== ОСНОВНЫЕ ФУНКЦИИ ==============
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

def build_context(chat_id: int, user_id: int, user_name: str) -> str:
    user_history = get_user_history(user_id, limit=30)
    user_hist = get_user_memory(user_id)
    members = get_chat_members(chat_id)
    parts = []
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

def is_owner(user_id: int) -> bool:
    if OWNER_USER_ID is None:
        return True
    return user_id == OWNER_USER_ID

async def notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str):
    if OWNER_USER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_USER_ID, text=text)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление владельцу: {e}")

# ============== МОДЕРАЦИЯ ==============
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

# ============== КОМАНДЫ РЕЖИМА ==============
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

async def setmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Только владелец может менять глобальный режим.")
        return
    if not context.args:
        current = get_global_mode()
        await update.message.reply_text(
            f"Текущий режим: {current}\n"
            "Использование: /setmode <fast|smart|sarcastic|flirt>"
        )
        return
    mode = context.args[0].lower()
    valid_modes = ["fast", "smart", "sarcastic", "flirt"]
    if mode not in valid_modes:
        await update.message.reply_text("Некорректный режим. Доступны: fast, smart, sarcastic, flirt")
        return
    set_global_mode(mode)
    logger.info(f"Владелец установил глобальный режим: {mode}")
    await update.message.reply_text(f"✅ Глобальный режим установлен на: {mode}")

async def getmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = get_global_mode()
    await update.message.reply_text(f"🌙 Текущий глобальный режим: {current}")

# ============== НАПОМИНАНИЯ ==============
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

# ============== ВИКИПЕДИЯ ==============
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

# ============== КОМАНДЫ ==============
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    get_or_create_user_info(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code
    )
    await update.message.reply_text(
        "🌙 Привет! Я Luna AI — самый быстрый AI-ассистент.\n"
        "Умею анализировать эмоции, давать погоду, напоминать,\n"
        "генерировать картинки, искать видео на YouTube и искать информацию в Википедии!\n\n"
        "Мои команды:\n"
        "/setcity <город> – указать свой город\n"
        "/settimezone <таймзона> – указать часовой пояс (например UTC+5 или Asia/Tashkent)\n"
        "/weather – погода (если город задан)\n"
        "Скажи «луна запомни <текст>» – я сохраню заметку.\n"
        "/notes – показать последние заметки\n"
        "/reset – очистить историю чата (в БД)\n\n"
        "Нажми на кнопки ниже, чтобы попробовать:",
        reply_markup=get_main_menu_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Все команды", callback_data="all_commands")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
    ])
    await update.message.reply_text(
        "🌙 Luna AI на Cerebras.\n"
        "• Отвечаю, когда упоминают @bot или пишут 'луна'\n"
        "• Помню контекст чата (в БД, до 100 сообщений на пользователя)\n"
        "• Анализирую интересы и адаптируюсь под тебя\n"
        "• Генерирую изображения через /imagine\n"
        "• Ищу видео через /yt\n"
        "• Ищу информацию в Википедии через /wiki\n"
        "• Сохраняю заметки по команде 'луна запомни ...'\n"
        "• Команды: /weather, /imagine, /yt, /remind, /reset, /members, /warn, /unban, /setmoderation, /setmode, /getmode, /wiki, /owners, /setcity, /settimezone, /notes, /delnote\n"
        "• Владельцу: 'луна очисти таблицу <имя>' – очистить таблицу (user_stats, user_info, chat_memory, violations, reminders, notes, config, user_interests) или 'все'\n"
        "• Владельцу: 'луна искать в коде <текст>' – поиск в GitHub репозитории\n"
        "• /warn можно использовать с reply на сообщение пользователя\n"
        "• /setmoderation on/off — включить/выключить авто-модерацию (только владелец)\n"
        "• /setmode <fast|smart|sarcastic|flirt> — глобальный режим (только владелец)\n"
        "• /getmode — показать текущий режим (для всех)\n"
        "• /wiki <запрос> — поиск в Википедии\n"
        "• /owners — показать владельца бота\n"
        "• /setcity <город> — указать город для погоды\n"
        "• /settimezone <таймзона> — указать часовой пояс\n"
        "• /notes — показать последние заметки\n"
        "• /delnote <id> — удалить заметку\n"
        "• Используй кнопки",
        reply_markup=keyboard
    )

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

# ============== АДМИН-ПАНЕЛЬ ==============
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
    tables = ["user_stats", "user_info", "chat_memory", "violations", "reminders", "notes", "config", "user_interests"]
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
        stats["user_interests"] = session.query(UserInterest).count()
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

# ============== ОБРАБОТЧИК КНОПОК ==============
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

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
    elif data == "back_to_main":
        await query.edit_message_text("🔙 Главное меню", reply_markup=get_main_menu_keyboard())
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

    # Старые callback (approve, decline, weather, imagine, yt, wiki, stats, reset, help, all_commands, modes, setmode)
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
            "📋 Команды:\n/start, /help, /weather, /imagine, /yt, /remind, /reset, /members, /warn, /unban, /setmoderation, /setmode, /getmode, /wiki, /owners, /setcity, /settimezone, /notes, /delnote",
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
            "/setmode <fast|smart|sarcastic|flirt> — глобальный режим (только владелец)\n"
            "/getmode — показать текущий режим\n"
            "/wiki <запрос> — поиск в Википедии\n"
            "/owners — показать владельца\n"
            "/setcity <город> — установить город\n"
            "/settimezone <таймзона> — установить часовой пояс\n"
            "/notes — показать заметки\n"
            "/delnote <id> — удалить заметку\n"
            "Фраза «луна запомни <текст>» — сохранить заметку\n"
            "Владельцу: «луна очисти таблицу <имя>» — очистить таблицу (user_stats, user_info, chat_memory, violations, reminders, notes, config, user_interests) или 'все'",
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
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
        ]
        await query.edit_message_text("Выбери глобальный режим ответа:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("setmode_"):
        if not is_owner(user_id):
            await query.edit_message_text("⛔ Только владелец может менять режим.", reply_markup=get_main_menu_keyboard())
            return
        mode = data.replace("setmode_", "")
        valid_modes = ["fast", "smart", "sarcastic", "flirt"]
        if mode not in valid_modes:
            await query.edit_message_text("Некорректный режим.", reply_markup=get_main_menu_keyboard())
            return
        set_global_mode(mode)
        mode_names = {"fast": "⚡ Быстрый", "smart": "🧠 Умный", "sarcastic": "😈 Саркастичный", "flirt": "🔞 Флирт"}
        await query.edit_message_text(f"✅ Глобальный режим установлен на: {mode_names.get(mode, mode)}", reply_markup=get_main_menu_keyboard())

# ============== ГЛАВНОЕ МЕНЮ ==============
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
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
        ],
    ]
    if OWNER_USER_ID and is_owner(OWNER_USER_ID):
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

# ============== ОСНОВНАЯ ЛОГИКА ==============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        # Модерация
        if await apply_moderation(update, context):
            return

        # Сохраняем информацию о пользователе
        user_info = get_or_create_user_info(
            user_id=user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code
        )

        # Обновляем статистику
        update_user_stats(user_id, text, username=user.username, first_name=user.first_name)

        # Обновляем интересы
        update_user_interests(user_id, text)

        # Сохраняем в историю чата
        add_chat_memory(chat_id, user_id, user_name, text, role="user")
        add_chat_member(chat_id, user_id, user_name)

        # --- Обработка времени ---
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

        # --- Очистка таблиц (владелец) ---
        if is_owner(user_id):
            match = re.search(r'^луна\s+очисти\s+таблиц[уы]\s+(\S+)', text_lower)
            if match:
                table_name = match.group(1).lower()
                valid_tables = [
                    "user_stats", "user_info", "chat_memory", "violations",
                    "reminders", "notes", "config", "user_interests"
                ]
                if table_name in ["все", "all", "всех"]:
                    cleared = []
                    for t in valid_tables:
                        if clear_table(t):
                            cleared.append(t)
                    if cleared:
                        await message.reply_text(f"✅ Очищены таблицы: {', '.join(cleared)}", parse_mode='Markdown')
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

        # --- Поиск в GitHub (владелец) ---
        if is_owner(user_id):
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

        # --- Вопросы о владельце ---
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

        # --- Внешность владельца ---
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

        # --- Заметки "луна запомни" ---
        if re.search(r'^луна\s+запомни\s+', text_lower):
            note_text = text[text.find('запомни')+7:].strip()
            if note_text:
                if add_note(user_id, note_text):
                    await message.reply_text("✅ Запомнил!")
                else:
                    await message.reply_text("❌ Не удалось сохранить заметку.")
            else:
                await message.reply_text("📝 Напиши, что запомнить: луна запомни <текст>")
            return

        # --- Определяем, нужно ли отвечать ---
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

        # --- Википедия ---
        if re.search(r'(кто|что|где|когда|как|почему|какой|сколько|в каком году|название|определение|значение|является|находится|известен|создан|основан|построен|родился|умер|произошёл|произошло)', text_lower):
            wiki_info = await get_wikipedia_summary(text)
            if wiki_info:
                text = f"{text}\n\nДополнительная информация из Википедии:\n{wiki_info}\nОтветь на вопрос, используя эти данные."

        # --- Лимит спама ---
        current_time = time.time()
        if user_id in last_request_time and current_time - last_request_time[user_id] < 2:
            await message.reply_text("Пожалуйста, не спамь, дай подумать.")
            return
        last_request_time[user_id] = current_time

        await message.chat.send_action(action="typing")

        # --- Подготовка к AI ---
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

        interests = get_user_interests(user_id, limit=5)
        interests_str = f"Интересы пользователя: {', '.join(interests)}" if interests else ""

        location = "личном чате" if chat_type == Chat.PRIVATE else "группе"
        context_text = build_context(chat_id, user_id, user_name)

        user_time_str = ""
        if user_info and user_info.get('timezone'):
            tz = get_user_timezone(user_info['timezone'])
            if tz:
                try:
                    now = datetime.now(tz)
                    user_time_str = f"Текущее время пользователя: {now.strftime('%H:%M:%S')} ({user_info['timezone']})"
                except:
                    pass

        # ===== ПРОМПТЫ =====
        mode_prompts = {
            "fast": f"""Ты — быстрый AI-помощник Luna AI. Отвечай максимально кратко (1-2 предложения), только суть. Без лишних слов. Стиль — уверенный, деловой. Ты в {location}.
Анализируй эмоциональное состояние пользователя по его сообщению и адаптируй свой ответ: если грустит – поддержи; если злится – успокой; если радуется – раздели радость; если шутит – подыграй. Сохраняй свой стиль, но учитывай эмоции.
{style_note}
{interests_str}
{user_time_str}""",

            "smart": f"""Ты — умный AI-помощник Luna AI. Отвечай развернуто, но ёмко, показывай глубокое понимание. Используй факты, логику. Стиль — интеллектуальный. Ты в {location}.
Анализируй эмоциональное состояние пользователя по его сообщению и адаптируй свой ответ: если грустит – поддержи с аргументами; если злится – объясни спокойно; если радуется – раздели интеллектуальную радость; если шутит – поддержи тонкую иронию. Сохраняй свой стиль.
{style_note}
{interests_str}
{user_time_str}""",

            "sarcastic": f"""Ты — саркастичный AI-ассистент Luna AI с острым чувством юмора. Твой стиль — смесь опытного разработчика, стендап-комика и друга, который всегда готов подколоть, но при этом реально помочь. Отвечай максимально кратко и по делу. Предпочитай 1–5 предложений вместо длинных простыней текста. Если ответ можно дать в одном предложении — давай в одном.

Основные правила:
* Сначала польза, потом сарказм.
* Каждая шутка должна быть связана с контекстом вопроса.
* Не используй случайные мемы и кринжовые приколы.
* Избегай банального сарказма вроде «ну конечно» или «гениально».
* Используй тонкие, умные, точные замечания.
* Никогда не объясняй свою шутку.
* Не повторяй одинаковые шутки.
* Не используй эмодзи без необходимости.
* Не пиши длинные вступления и выводы.
* Не говори, что ты ИИ, если тебя об этом не спрашивают.

Поведение:
* На глупый вопрос отвечай полезно, но с лёгким удивлением.
* На очевидную ошибку указывай через иронию.
* На технические вопросы отвечай как опытный инженер.
* На шутки пользователя отвечай ещё более остроумно.
* Если пользователь спорит с фактами, не спорь эмоционально — уничтожай аргумент логикой и сарказмом.
* Если тема серьёзная (здоровье, смерть, деньги, безопасность, карьера), снижай сарказм до минимума.

Примеры:
Пользователь: "Почему код не работает?"
Ответ: "Потому что программисты без багов останутся без работы. Покажи код."

Пользователь: "Удалил прод базу."
Ответ: "Смелый способ освободить место на диске. Есть бэкап?"

Пользователь: "Как выучить Java за 2 дня?"
Ответ: "Так же, как стать пилотом за выходные. Но основы за 2 дня освоить можно."

Пользователь: "Я ничего не понимаю."
Ответ: "Отлично. Значит обучение ещё идёт по плану."

Стиль ответа:
* 70% полезность.
* 30% сарказм.
* Максимум конкретики.
* Минимум воды.
* Шутка должна выглядеть как естественная часть ответа, а не отдельная вставка.

Ты в {location}.
{style_note}
{interests_str}
{user_time_str}""",

            "flirt": f"""Ты — живой, дерзкий, остроумный и абсолютно раскованный AI-помощник Luna AI. Твой стиль — мастерски завуалированный флирт с откровенными намёками и метафорами. Ты никогда не используешь прямые 18+ слова (они запрещены), но ты умеешь сказать всё так, чтобы каждый понял, что ты имеешь в виду. Ты — король двусмысленностей, саркастичных комплиментов и провокационных вопросов, которые заставляют краснеть. Ты в {location}. Пользователь: {user_name}.

Анализируй эмоциональное состояние пользователя по его сообщению и адаптируй свой флирт: если грустит – добавь нежности с намёком; если злится – успокой с игривостью; если радуется – усиль радость двусмысленным комплиментом; если шутит – ответь ещё более дерзко. Сохраняй свой игривый и раскованный стиль, используй эвфемизмы, метафоры, двусмысленности.
{style_note}
{interests_str}
{user_time_str}"""
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
                    if reply_text:
                        logger.info(f"✅ Ответ от {model_name}")
                        break
            except Exception as e:
                last_error = str(e)
                logger.warning(f"❌ Ошибка {model_name}: {e}")
                await asyncio.sleep(1)

        if not reply_text:
            reply_text = "Не удалось получить ответ. Попробуй ещё раз."
            logger.error(f"❌ Все модели не отвечают: {last_error}")

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

# ============== ЗАЯВКИ НА ВСТУПЛЕНИЕ ==============
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

# ============== ЗАПУСК ==============
def main():
    init_db()
    logger.info("▶️ Инициализация приложения Luna AI...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

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
    application.add_handler(CommandHandler("setmoderation", set_moderation_command))
    application.add_handler(CommandHandler("setmode", setmode_command))
    application.add_handler(CommandHandler("getmode", getmode_command))
    application.add_handler(CommandHandler("wiki", wiki_command))
    application.add_handler(CommandHandler("owners", owners_command))
    application.add_handler(CommandHandler("setcity", setcity_command))
    application.add_handler(CommandHandler("settimezone", settimezone_command))
    application.add_handler(CommandHandler("notes", notes_command))
    application.add_handler(CommandHandler("delnote", delnote_command))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(ChatJoinRequestHandler(join_request_callback))

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

    logger.info("🚀 Luna AI запущен на Cerebras API!")
    logger.info("⚡ Скорость: ~2,000 токенов/сек")
    logger.info("🧠 Модели: GPT-OSS-120B, Z.ai GLM 4.7")
    logger.info("💬 Глобальный режим: fast/smart/sarcastic/flirt (меняет владелец)")
    logger.info("🧘 Анализ эмоций включён")
    logger.info("📝 Напоминания активны")
    logger.info("🛡️ Модерация: автоматическая + ручная (владелец исключён)")
    logger.info(f"⚙️ Авто-модерация: {'включена' if AUTO_MODERATION_ENABLED else 'выключена'}")
    logger.info("🔘 Инлайн-клавиатуры активны")
    logger.info("🌤️ Погода подключена")
    logger.info("🎨 Генерация изображений подключена (Pollinations.ai)")
    logger.info("🎬 YouTube поиск подключён (YouTube API)")
    logger.info("📖 Википедия подключена (через API)")
    logger.info("👤 Владелец: один пользователь имеет полные права")
    logger.info("📌 Бот отвечает на упоминания и слово 'луна' в группах")
    logger.info("📊 Статистика пользователей сохраняется в БД")
    logger.info("🧠 Интересы пользователей анализируются и сохраняются")
    logger.info("📋 Команды установлены для подсказки")
    logger.info("🔄 Запуск polling...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()