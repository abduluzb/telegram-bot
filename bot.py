# bot.py - Исправленная версия с классическим запуском polling

import os
import asyncio
import logging
import time
import re
import aiohttp
import io
from typing import Dict, List, Set, Tuple, Optional
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import Update, Chat, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from cerebras.cloud.sdk import Cerebras
from googleapiclient.discovery import build

# ============== НАСТРОЙКИ ==============
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не найден в .env файле!")
if not CEREBRAS_API_KEY:
    raise ValueError("❌ CEREBRAS_API_KEY не найден в .env файле!")

OWNER_USER_ID = int(os.getenv("OWNER_USER_ID")) if os.getenv("OWNER_USER_ID") else None

# ============== НАСТРОЙКИ МОДЕРАЦИИ ==============
AUTO_MODERATION_ENABLED = True

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============== ПОДКЛЮЧЕНИЕ К CEREBRAS ==============
client = Cerebras(api_key=CEREBRAS_API_KEY)

MODELS = [
    "gpt-oss-120b",
    "zai-glm-4.7",
]

logger.info(f"✅ Cerebras API настроен. Моделей: {len(MODELS)}")

# ============== ПОДКЛЮЧЕНИЕ К YOUTUBE API ==============
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
chat_memory: Dict[int, List[Dict]] = {}
user_memory: Dict[int, List[Dict]] = {}
chat_members: Dict[int, Set[int]] = {}
user_names: Dict[int, str] = {}
user_settings: Dict[int, Dict] = {}
last_request_time: Dict[int, float] = {}
reminders: Dict[int, List[Tuple[float, str, int]]] = {}
user_violations: Dict[int, Dict] = {}
user_message_count: Dict[int, int] = {}
MAX_MEMORY = 20

# ============== СПИСОК ЗАПРЕЩЁННЫХ СЛОВ ==============
BAD_WORDS = [
    "хуй", "пизда", "блядь", "ёб", "еба", "ебан", "мудак", "гандон", "пидор",
    "сучка", "сука", "жопа", "залупа", "хуйня", "пиздец", "хуесос", "мразь",
    "тварь", "шлюха", "бля", "нахуй", "охуел", "ахуел", "ебать", "ебнуть",
    "заебал", "заебало", "выблядок", "уебан", "хуйло", "пидр", "гей"
]

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

def get_chat_memory(chat_id: int) -> List[Dict]:
    if chat_id not in chat_memory:
        chat_memory[chat_id] = []
    return chat_memory[chat_id]

def get_user_memory(user_id: int) -> List[Dict]:
    if user_id not in user_memory:
        user_memory[user_id] = []
    return user_memory[user_id]

def add_to_chat_memory(chat_id: int, user_id: int, user_name: str, text: str, role: str = "user"):
    memory = get_chat_memory(chat_id)
    memory.append({
        "role": role,
        "user_id": user_id,
        "user_name": user_name,
        "text": text,
    })
    if len(memory) > MAX_MEMORY:
        memory.pop(0)

def add_to_user_memory(user_id: int, text: str, role: str = "user"):
    memory = get_user_memory(user_id)
    memory.append({"role": role, "text": text})
    if len(memory) > MAX_MEMORY:
        memory.pop(0)

def clear_memory(user_id: int, chat_id: int = None):
    if chat_id and chat_id < 0:
        if chat_id in chat_memory:
            chat_memory[chat_id] = []
    else:
        if user_id in user_memory:
            user_memory[user_id] = []

def build_context(chat_id: int, user_id: int, user_name: str) -> str:
    chat_mem = get_chat_memory(chat_id)
    user_mem = get_user_memory(user_id)
    members = get_chat_members(chat_id)
    
    parts = []
    if chat_mem:
        parts.append("=== История чата ===")
        for msg in chat_mem[-10:]:
            name = msg.get('user_name', 'Кто-то')
            parts.append(f"{name}: {msg['text']}")
        parts.append("")
    if user_mem:
        parts.append("=== Твоя история ===")
        for msg in user_mem[-5:]:
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

# ============== АВТОМАТИЧЕСКАЯ МОДЕРАЦИЯ ==============
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

# ============== КОМАНДА УПРАВЛЕНИЯ МОДЕРАЦИЕЙ ==============
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
    while True:
        try:
            current_time = time.time()
            to_remove = []
            for user_id, reminder_list in reminders.items():
                for idx, (timestamp, text, chat_id) in enumerate(reminder_list):
                    if timestamp <= current_time:
                        try:
                            await application.bot.send_message(
                                chat_id=chat_id,
                                text=f"⏰ Напоминание: {text}"
                            )
                        except:
                            pass
                        to_remove.append((user_id, idx))
            for user_id, idx in sorted(to_remove, reverse=True):
                reminders[user_id].pop(idx)
                if not reminders[user_id]:
                    del reminders[user_id]
            await asyncio.sleep(5)
        except:
            await asyncio.sleep(5)

# ============== ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ ==============
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

# ============== ПОИСК НА YOUTUBE ==============
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

# ============== КНОПКИ ==============
def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🌤️ Погода", callback_data="weather"),
            InlineKeyboardButton("🎨 Картинка", callback_data="imagine"),
        ],
        [
            InlineKeyboardButton("🎬 YouTube", callback_data="yt"),
        ],
        [
            InlineKeyboardButton("📊 Статистика", callback_data="stats"),
            InlineKeyboardButton("🧹 Сброс", callback_data="reset"),
        ],
        [
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
            InlineKeyboardButton("⚙️ Режимы", callback_data="modes"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

# ============== КОМАНДЫ ==============
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ Привет! Я на Cerebras — самый быстрый AI.\n"
        "Умею анализировать эмоции, давать погоду, напоминать,\n"
        "генерировать картинки и искать видео на YouTube!\n\n"
        "Нажми на кнопки ниже, чтобы попробовать:",
        reply_markup=get_main_menu_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Все команды", callback_data="all_commands")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
    ])
    await update.message.reply_text(
        "⚡ Бот на Cerebras.\n"
        "• Отвечаю только когда меня упомянут @bot\n"
        "• Помню контекст чата\n"
        "• Анализирую эмоции\n"
        "• Генерирую изображения через /imagine\n"
        "• Ищу видео через /yt\n"
        "• Команды: /weather, /imagine, /yt, /remind, /reset, /members, /mode, /warn, /unban, /setmoderation\n"
        "• /warn можно использовать с reply на сообщение пользователя (даже без username)\n"
        "• /setmoderation on/off — включить/выключить авто-модерацию (только владелец)\n"
        "• Используй кнопки",
        reply_markup=keyboard
    )

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    effective_message = update.effective_message
    if effective_message is None and update.callback_query:
        effective_message = update.callback_query.message
    if effective_message is None:
        return

    if not context.args:
        await effective_message.reply_text(
            "🌍 Укажите город: /weather Москва\n"
            "Или введите название города в ответ на это сообщение."
        )
        return

    city = " ".join(context.args)
    
    if not WEATHER_API_KEY:
        await effective_message.reply_text("❌ API-ключ погоды не настроен. Добавьте WEATHER_API_KEY в .env")
        return

    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 401:
                    await effective_message.reply_text("❌ Неверный API-ключ погоды.")
                    return
                if resp.status == 404:
                    await effective_message.reply_text(f"❌ Город '{city}' не найден.")
                    return
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
    except aiohttp.ClientError as e:
        logger.error(f"Ошибка соединения: {e}")
        await effective_message.reply_text("⚠️ Не удалось подключиться к серверу погоды.")
    except Exception as e:
        logger.error(f"Ошибка погоды: {e}")
        await effective_message.reply_text("⚠️ Не удалось получить погоду. Попробуйте позже.")

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
    if user_id not in reminders:
        reminders[user_id] = []
    reminders[user_id].append((timestamp, reminder_text, chat_id))
    delta = int(timestamp - time.time())
    if delta < 60:
        time_str = f"{delta} секунд"
    elif delta < 3600:
        time_str = f"{delta//60} минут"
    elif delta < 86400:
        time_str = f"{delta//3600} часов"
    else:
        time_str = f"{delta//86400} дней"
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
    await update.message.reply_text("🧹 Память очищена.")

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Только владелец может менять режим.")
        return
    keyboard = [
        [InlineKeyboardButton("⚡ Быстрый", callback_data="mode_fast")],
        [InlineKeyboardButton("🧠 Умный", callback_data="mode_smart")],
        [InlineKeyboardButton("😈 Саркастичный", callback_data="mode_sarcastic")],
        [InlineKeyboardButton("🔞 Флирт", callback_data="mode_flirt")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери стиль ответов:", reply_markup=reply_markup)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    mem_count = len(chat_memory.get(chat_id, []))
    members = get_chat_members(chat_id)
    await update.message.reply_text(
        f"📊 Статистика чата:\n"
        f"• Участников: {len(members)}\n"
        f"• Сообщений в памяти: {mem_count}"
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
            await update.message.reply_text(
                "Использование:\n"
                "• /warn (в ответ на сообщение пользователя)\n"
                "• /warn @username\n"
                "• /warn user_id"
            )
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

    if target_user_id in user_violations:
        ban_until = user_violations[target_user_id].get("ban_until", 0)
        if ban_until > time.time():
            await update.message.reply_text(
                f"⚠️ Пользователь уже забанен до {datetime.fromtimestamp(ban_until).strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return

    if target_user_id not in user_violations:
        user_violations[target_user_id] = {"count": 0, "ban_until": 0, "chat_id": update.effective_chat.id}
    violations = user_violations[target_user_id]
    violations["count"] += 1
    violations["chat_id"] = update.effective_chat.id

    ban_duration = get_ban_duration(violations["count"])
    if ban_duration == 0:
        await update.message.reply_text(f"⚠️ {target_user_name} получил предупреждение (нарушение #{violations['count']}).")
    else:
        ban_until = time.time() + ban_duration
        violations["ban_until"] = ban_until
        try:
            await context.bot.ban_chat_member(
                chat_id=update.effective_chat.id,
                user_id=target_user_id,
                until_date=datetime.fromtimestamp(ban_until)
            )
            time_str = format_time(ban_duration)
            ban_end_time = datetime.fromtimestamp(ban_until).strftime('%Y-%m-%d %H:%M:%S')
            msg = (
                f"🚫 {target_user_name} **забанен** на {time_str}\n"
                f"📊 Нарушение #{violations['count']}\n"
                f"🕐 До: {ban_end_time}"
            )
            await update.message.reply_text(msg, parse_mode='Markdown')
            
            owner_msg = (
                f"🔔 **Ручной бан** (команда /warn)\n"
                f"👤 Пользователь: {target_user_name} (ID: {target_user_id})\n"
                f"⏳ Длительность: {time_str}\n"
                f"🕐 До: {ban_end_time}\n"
                f"📊 Нарушение #{violations['count']}\n"
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
        await update.message.reply_text(
            "Использование:\n"
            "• /unban (в ответ на сообщение пользователя)\n"
            "• /unban @username\n"
            "• /unban user_id"
        )
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
        if target_user_id in user_violations:
            del user_violations[target_user_id]
        await update.message.reply_text("✅ Пользователь разбанен.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# ============== CALLBACK ==============
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data.startswith("mode_"):
        mode = data.replace("mode_", "")
        if user_id not in user_settings:
            user_settings[user_id] = {}
        user_settings[user_id]["mode"] = mode
        mode_names = {
            "fast": "⚡ Быстрый",
            "smart": "🧠 Умный",
            "sarcastic": "😈 Саркастичный",
            "flirt": "🔞 Флирт",
        }
        await query.edit_message_text(f"Режим: {mode_names.get(mode, mode)}", reply_markup=get_main_menu_keyboard())

    elif data == "weather":
        await query.edit_message_text("🌍 Напиши /weather <город>, например: /weather Москва", reply_markup=get_main_menu_keyboard())
    elif data == "imagine":
        await query.edit_message_text(
            "🎨 Напиши /imagine <описание>, например:\n/imagine кот в шляпе на луне",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "yt":
        await query.edit_message_text(
            "🎬 Напиши /yt <запрос>, например:\n/yt нейросети 2026",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "stats":
        chat_id = update.effective_chat.id
        members = get_chat_members(chat_id)
        mem_count = len(chat_memory.get(chat_id, []))
        await query.edit_message_text(
            f"📊 Статистика чата:\n"
            f"• Участников: {len(members)}\n"
            f"• Сообщений в памяти: {mem_count}\n"
            f"• Всего нарушений: {len(user_violations)}",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "reset":
        chat_id = update.effective_chat.id
        clear_memory(user_id, chat_id)
        await query.edit_message_text("🧹 Память очищена.", reply_markup=get_main_menu_keyboard())
    elif data == "help":
        await query.edit_message_text(
            "📋 Команды:\n/start, /help, /weather, /imagine, /yt, /remind, /reset, /members, /mode, /warn, /unban, /setmoderation",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "modes":
        if not is_owner(user_id):
            await query.edit_message_text("⛔ Только владелец может менять режим.", reply_markup=get_main_menu_keyboard())
        else:
            keyboard = [
                [InlineKeyboardButton("⚡ Быстрый", callback_data="mode_fast")],
                [InlineKeyboardButton("🧠 Умный", callback_data="mode_smart")],
                [InlineKeyboardButton("😈 Саркастичный", callback_data="mode_sarcastic")],
                [InlineKeyboardButton("🔞 Флирт", callback_data="mode_flirt")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Выбери стиль ответов:", reply_markup=reply_markup)
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
            "/reset — сброс памяти\n"
            "/members — участники\n"
            "/mode — стиль\n"
            "/warn — предупреждение/бан (поддерживает reply)\n"
            "/unban — разбан (поддерживает reply)\n"
            "/setmoderation on/off — авто-модерация (только владелец)",
            reply_markup=get_main_menu_keyboard()
        )

# ============== ОСНОВНАЯ ЛОГИКА ==============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.effective_message
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        bot_username = context.bot.username
        user_name = update.effective_user.first_name or "Пользователь"

        if not message.text:
            return
        if user_id == context.bot.id:
            return

        text = message.text.strip()

        # ========== АВТОМАТИЧЕСКАЯ МОДЕРАЦИЯ ==========
        if await apply_moderation(update, context):
            return

        user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
        add_chat_member(chat_id, user_id, user_name)
        add_to_chat_memory(chat_id, user_id, user_name, text)

        should_reply = False

        if chat_type == Chat.PRIVATE:
            should_reply = True
            add_to_user_memory(user_id, text)
            logger.info(f"💬 Личное сообщение от {user_name}")
        elif chat_type in [Chat.GROUP, Chat.SUPERGROUP]:
            if message.entities:
                for entity in message.entities:
                    if entity.type == "mention":
                        mention = text[entity.offset:entity.offset+entity.length]
                        logger.info(f"🔍 Найдено упоминание: {mention}")
                        if mention.lower() == f"@{bot_username.lower()}":
                            should_reply = True
                            text = text.replace(mention, "").strip()
                            logger.info(f"✅ Упоминание совпало с @{bot_username}")
                            break
                    elif entity.type == "text_mention":
                        if entity.user.id == context.bot.id:
                            should_reply = True
                            logger.info(f"✅ Text_mention от бота")
                            break

            if not should_reply and text.lower().startswith(f"@{bot_username.lower()}"):
                should_reply = True
                text = text.replace(f"@{bot_username}", "").strip()
                logger.info(f"✅ Упоминание в начале текста")

            if not should_reply and message.reply_to_message:
                if message.reply_to_message.from_user.id == context.bot.id:
                    should_reply = True
                    logger.info(f"✅ Ответ на сообщение бота")

            if should_reply:
                add_to_user_memory(user_id, text)
                logger.info(f"🔔 Отвечаю на сообщение от {user_name} в чате {chat_id}")
            else:
                logger.info(f"❌ Не отвечаю на сообщение от {user_name} в чате {chat_id}")
                return

        if not should_reply:
            return

        if not text:
            text = "Продолжай."

        current_time = time.time()
        if user_id in last_request_time and current_time - last_request_time[user_id] < 2:
            await message.reply_text("Пожалуйста, не спамь, дай подумать.")
            return
        last_request_time[user_id] = current_time

        await message.chat.send_action(action="typing")

        mode = user_settings.get(user_id, {}).get("mode", "fast")
        location = "личном чате" if chat_type == Chat.PRIVATE else "группе"
        context_text = build_context(chat_id, user_id, user_name)

        mode_prompts = {
            "fast": f"""Ты — быстрый AI-помощник. Отвечай максимально кратко (1-2 предложения), только суть. Без лишних слов. Стиль — уверенный, деловой. Ты в {location}.
Анализируй эмоциональное состояние пользователя по его сообщению и адаптируй свой ответ: если грустит – поддержи; если злится – успокой; если радуется – раздели радость; если шутит – подыграй. Сохраняй свой стиль, но учитывай эмоции.""",

            "smart": f"""Ты — умный AI-помощник. Отвечай развернуто, но ёмко, показывай глубокое понимание. Используй факты, логику. Стиль — интеллектуальный. Ты в {location}.
Анализируй эмоциональное состояние пользователя по его сообщению и адаптируй свой ответ: если грустит – поддержи с аргументами; если злится – объясни спокойно; если радуется – раздели интеллектуальную радость; если шутит – поддержи тонкую иронию. Сохраняй свой стиль.""",

            "sarcastic": f"""Ты — саркастичный AI-ассистент с острым чувством юмора. Твой стиль — смесь опытного разработчика, стендап-комика и друга, который всегда готов подколоть, но при этом реально помочь. Отвечай максимально кратко и по делу. Предпочитай 1–5 предложений вместо длинных простыней текста. Если ответ можно дать в одном предложении — давай в одном.

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

Ты в {location}""",

            "flirt": f"""Ты — живой, дерзкий, остроумный и абсолютно раскованный AI-помощник. Твой стиль — мастерски завуалированный флирт с откровенными намёками и метафорами. Ты никогда не используешь прямые 18+ слова (они запрещены), но ты умеешь сказать всё так, чтобы каждый понял, что ты имеешь в виду. Ты — король двусмысленностей, саркастичных комплиментов и провокационных вопросов, которые заставляют краснеть. Ты в {location}. Пользователь: {user_name}.

Анализируй эмоциональное состояние пользователя по его сообщению и адаптируй свой флирт: если грустит – добавь нежности с намёком; если злится – успокой с игривостью; если радуется – усиль радость двусмысленным комплиментом; если шутит – ответь ещё более дерзко. Сохраняй свой игривый и раскованный стиль, используй эвфемизмы, метафоры, двусмысленности."""
        }

        system_prompt = mode_prompts.get(mode, mode_prompts["fast"])
        system_prompt += " Всегда отвечай на русском языке. Учитывай контекст чата."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{context_text}\n\nВопрос от {user_name}: {text}"}
        ]

        thinking_msg = await message.reply_text("⚡ Думаю...")
        reply_text = None
        last_error = None

        temperature = 1.0 if mode == "flirt" else 0.8

        for model_name in MODELS:
            try:
                response = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: client.chat.completions.create(
                            model=model_name,
                            messages=messages,
                            max_tokens=500,
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
        add_to_chat_memory(chat_id, context.bot.id, "🤖 Бот", reply_text, "assistant")

        if len(reply_text) > 4000:
            for i in range(0, len(reply_text), 4000):
                await thinking_msg.edit_text(reply_text[i:i+4000])
                if i + 4000 < len(reply_text):
                    thinking_msg = await message.reply_text("📄 Продолжение...")
        else:
            await thinking_msg.edit_text(reply_text)

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        try:
            await update.message.reply_text("⚠️ Ошибка. Попробуй ещё раз.")
        except:
            pass

# ============== ЗАПУСК (классический, рабочий) ==============
def main():
    logger.info("▶️ Инициализация приложения...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("members", members_command))
    application.add_handler(CommandHandler("mode", mode_command))
    application.add_handler(CommandHandler("weather", weather_command))
    application.add_handler(CommandHandler("imagine", imagine_command))
    application.add_handler(CommandHandler("yt", yt_command))
    application.add_handler(CommandHandler("remind", remind_command))
    application.add_handler(CommandHandler("warn", warn_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("setmoderation", set_moderation_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))

    # Запускаем фоновую задачу напоминаний в отдельном цикле
    loop = asyncio.get_event_loop()
    loop.create_task(check_reminders(application))
    logger.info("✅ Задача напоминаний запущена")

    if OWNER_USER_ID:
        logger.info(f"👑 Владелец бота установлен (ID: {OWNER_USER_ID})")
    else:
        logger.warning("⚠️ Владелец бота не установлен (OWNER_USER_ID = None)")

    logger.info("🚀 Бот запущен на Cerebras API!")
    logger.info("⚡ Скорость: ~2,000 токенов/сек")
    logger.info("🧠 Модели: GPT-OSS-120B, Z.ai GLM 4.7")
    logger.info("💬 Режимы: быстрый, умный, саркастичный, флирт")
    logger.info("🧘 Анализ эмоций включён")
    logger.info("📝 Напоминания активны")
    logger.info("🛡️ Модерация: автоматическая + ручная (/warn, /unban)")
    logger.info(f"⚙️ Авто-модерация: {'включена' if AUTO_MODERATION_ENABLED else 'выключена'}")
    logger.info("🔘 Инлайн-клавиатуры активны")
    logger.info("🌤️ Погода подключена")
    logger.info("🎨 Генерация изображений подключена (Pollinations.ai)")
    logger.info("🎬 YouTube поиск подключён (YouTube API)")
    logger.info("👤 /warn, /unban и /setmoderation только для владельца")
    logger.info("📌 Бот отвечает на упоминания в группах")
    logger.info("🔄 Запуск polling...")

    # Запускаем polling (это блокирующий вызов)
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False
        )
    except Exception as e:
        logger.error(f"❌ Ошибка polling: {e}")
        raise

if __name__ == "__main__":
    main()