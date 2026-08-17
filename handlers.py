import re
import time
import asyncio
import io
import os
import uuid
import aiohttp
from datetime import datetime, timedelta

from telegram import Update, Chat, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from config import (
    logger,
    OWNER_USER_ID,
    OWNER_NAME,
    OWNER_DESCRIPTION,
    pending_requests,
    MODELS,
    disabled_chats,
    SHAZAM_API_KEY,
    WEATHER_API_KEY,
    AUTO_MODERATION_ENABLED   # <-- добавлено
)
from utils import (
    get_chat_members,
    add_chat_member,
    get_user_memory,
    add_to_user_memory,
    clear_memory,
    is_owner,
    notify_owner,
    get_user_timezone,
    parse_time,
    format_time,
    get_wikipedia_summary,
    search_github_code,
    get_github_file_content,
    last_request_time,         # <-- добавлено
    user_names                 # <-- добавлено
)
from api_clients import client, youtube, spotify
from database import (
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
    update_daily_stats,
    get_detailed_stats,
    get_top_users
)
from moderation import apply_moderation, contains_bad_words
from instagram import (
    is_instagram_url,
    download_instagram_video,
    download_instagram_audio,
    recognize_music_shazam,
    search_youtube_music
)
from music_trailer import (
    trailer_command,
    trailer_select_callback,
    music_command,
    music_yt_select_callback,
    music_cancel_callback
)
from admin import (
    admin_panel_start,
    admin_callback,
    handle_admin_text_input,
    handle_admin_photo_input,
    cancel_admin_input,
    skip_photo_input,
    admin_panel,
    clear_table_menu,
    db_stats,
    search_code_prompt
)
from chat_management import groups_command, manage_chats, chat_manage, chat_disable, chat_enable

# ----------------------------------------------------------------------
# КОМАНДЫ
# ----------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔥 start_command вызвана")
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
        "генерировать картинки, искать видео на YouTube и Instagram, и искать информацию в Википедии!\n\n"
        "🎬 *Новое!* Трейлеры фильмов — команда /trailer <название> (скачиваю MP4)\n"
        "🎵 *Новое!* Поиск музыки с выбором трека — /music <название> (скачиваю аудио)\n"
        "📥 *Новое!* Просто отправьте мне ссылку на Instagram (Reels/пост) — я скачаю видео!\n"
        "🎵 *Супер!* Под видео из Instagram будут кнопки:\n"
        "   — «Скачать аудио» (из этого Reels)\n"
        "   — «Найти полную версию» (распознаю через Shazam и скачаю с YouTube)\n\n"
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
        "• Скачиваю видео из Instagram по ссылке\n"
        "• Под видео есть кнопки «Скачать аудио» и «Найти полную версию» (Shazam)\n"
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

    all_chats = list(get_chat_members(0).keys())  # используем get_chat_members для доступа к словарю
    # На самом деле нужно брать chat_members из utils, но у нас есть глобальная переменная, можно импортировать напрямую
    from utils import chat_members
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
                    parse_mode=None
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
                    parse_mode=None
                )
            success += 1
        except:
            pass

    await status_msg.edit_text(
        f"✅ Рассылка завершена.\n"
        f"📨 Успешно: {success}\n"
        f"❌ Ошибок: {errors}"
    )

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

# ----------------------------------------------------------------------
# КНОПКИ ГЛАВНОГО МЕНЮ
# ----------------------------------------------------------------------

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
        keyboard.append([InlineKeyboardButton("📋 Управление чатами", callback_data="manage_chats")])
    return InlineKeyboardMarkup(keyboard)

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

    # Обработчики главного меню
    if data == "weather":
        await query.edit_message_text("🌍 Напиши /weather <город>", reply_markup=get_main_menu_keyboard())
    elif data == "imagine":
        await query.edit_message_text("🎨 Напиши /imagine <описание>", reply_markup=get_main_menu_keyboard())
    elif data == "yt":
        await query.edit_message_text("🎬 Напиши /yt <запрос>", reply_markup=get_main_menu_keyboard())
    elif data == "wiki":
        await query.edit_message_text("📖 Напиши /wiki <запрос>", reply_markup=get_main_menu_keyboard())
    elif data == "stats":
        chat_id = update.effective_chat.id
        members = get_chat_members(chat_id)
        await query.edit_message_text(
            f"📊 Статистика чата:\n• Участников: {len(members)}",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "reset":
        await query.edit_message_text("🧹 Память и история чата очищены (в БД).", reply_markup=get_main_menu_keyboard())
    elif data == "help":
        await query.edit_message_text(
            "📋 Команды: /start, /help, /weather, /imagine, /yt, /remind, /reset, /members, /warn, /unban, /setmoderation, /setmode, /getmode, /wiki, /owners, /setcity, /settimezone, /notes, /delnote, /broadcast, /admin, /stats_detail, /music, /trailer",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "back_to_menu":
        await query.edit_message_text("🔙 Главное меню", reply_markup=get_main_menu_keyboard())
    elif data == "all_commands":
        await query.edit_message_text(
            "📋 Полный список:\n/start – меню\n/help – помощь\n/weather – погода\n/imagine – картинка\n/yt – YouTube\n/remind – напоминание\n/reset – сброс памяти\n/members – участники\n/warn – предупреждение\n/unban – разбан\n/setmoderation – модерация\n/setmode – режим\n/getmode – текущий режим\n/wiki – Википедия\n/owners – владелец\n/setcity – город\n/settimezone – таймзона\n/notes – заметки\n/delnote – удалить заметку\n/broadcast – рассылка\n/admin – админ-панель\n/stats_detail – статистика\n/music – музыка\n/trailer – трейлеры",
            reply_markup=get_main_menu_keyboard()
        )
    elif data == "modes":
        if not is_owner(user_id):
            await query.edit_message_text("⛔ Только владелец.", reply_markup=get_main_menu_keyboard())
            return
        keyboard = [
            [InlineKeyboardButton("⚡ Быстрый", callback_data="setmode_fast")],
            [InlineKeyboardButton("🧠 Умный", callback_data="setmode_smart")],
            [InlineKeyboardButton("😈 Саркастичный", callback_data="setmode_sarcastic")],
            [InlineKeyboardButton("🔞 Флирт", callback_data="setmode_flirt")],
            [InlineKeyboardButton("🌀 Авто", callback_data="setmode_auto")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
        ]
        await query.edit_message_text("Выбери глобальный режим ответа:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("setmode_"):
        if not is_owner(user_id):
            await query.edit_message_text("⛔ Только владелец.", reply_markup=get_main_menu_keyboard())
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
            "auto": "🌀 Авто"
        }
        await query.edit_message_text(f"✅ Режим установлен: {mode_names.get(mode, mode)}", reply_markup=get_main_menu_keyboard())
    elif data == "open_admin_panel":
        await query.edit_message_text("👑 Загружаю админ-панель...")
        await admin_panel_start(update, context)
    elif data == "music":
        await query.edit_message_text("🎵 Напиши /music <название песни>", reply_markup=get_main_menu_keyboard())
    elif data == "trailer":
        await query.edit_message_text("🎬 Напиши /trailer <название фильма>", reply_markup=get_main_menu_keyboard())
    elif data == "manage_chats":
        await manage_chats(update, context)
    elif data.startswith("chat_manage_"):
        chat_id = int(data.split("_")[2])
        await chat_manage(update, context, chat_id)
    elif data.startswith("chat_disable_"):
        chat_id = int(data.split("_")[2])
        await chat_disable(update, context, chat_id)
    elif data.startswith("chat_enable_"):
        chat_id = int(data.split("_")[2])
        await chat_enable(update, context, chat_id)
    else:
        await query.edit_message_text("❌ Неизвестная команда")

# ----------------------------------------------------------------------
# ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ
# ----------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔥 handle_message вызвана")
    if update.message:
        logger.info(f"🔥 Сообщение: {update.message.text} от {update.effective_user.id}")
    else:
        logger.info("🔥 Обновление без сообщения")

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

        # Проверка: если чат отключён — игнорируем
        if chat_type in [Chat.GROUP, Chat.SUPERGROUP] and chat_id in disabled_chats:
            logger.info(f"Чат {chat_id} отключён владельцем, сообщение игнорируется.")
            return

        if await apply_moderation(update, context):
            return

        # ПРОВЕРКА НА INSTAGRAM
        if is_instagram_url(text):
            status_msg = await message.reply_text("📥 Скачиваю видео из Instagram...")
            video_path = await download_instagram_video(text)
            
            if video_path:
                try:
                    audio_id = uuid.uuid4().hex
                    if 'instagram_audio_requests' not in context.user_data:
                        context.user_data['instagram_audio_requests'] = {}
                    context.user_data['instagram_audio_requests'][audio_id] = text
                    
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🎵 Скачать аудио (из Reels)", callback_data=f"ig_audio_{audio_id}")],
                        [InlineKeyboardButton("🎶 Найти полную версию", callback_data=f"ig_find_full_{audio_id}")]
                    ])
                    
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=open(video_path, 'rb'),
                        caption="🎬 Видео из Instagram",
                        reply_markup=keyboard
                    )
                    await status_msg.delete()
                except Exception as e:
                    logger.error(f"Ошибка отправки Instagram видео: {e}")
                    await status_msg.edit_text(f"❌ Ошибка при отправке видео: {e}")
                finally:
                    if video_path and os.path.exists(video_path):
                        try:
                            os.remove(video_path)
                        except:
                            pass
            else:
                await status_msg.edit_text("❌ Не удалось скачать видео. Проверьте ссылку.\n\nВозможные причины:\n- Ссылка ведёт на приватный аккаунт\n- Видео недоступно\n- Ссылка недействительна")
            return

        # ---- ДАЛЕЕ СТАНДАРТНАЯ ЛОГИКА ----
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

        # ЗАПОМНИ ИМЯ
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

        # ЗАМЕТКИ
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

        # ВРЕМЯ
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

        # КОМАНДЫ ВЛАДЕЛЬЦА
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
                    await status_msg.edit_text(f"❌ Не удалось загрузить файл `{file_path}`.")
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
        custom_name = user_info.get('custom_name') if user_info else None
        location = "личном чате" if chat_type == Chat.PRIVATE else "группе"

        mode_prompts = {
            "fast": f"""Ты — Luna AI. Отвечай максимально кратко (1-2 предложения) и точно, но с лёгкой иронией. Без воды. Сарказм приветствуется.
Пользователь: {user_name}. Вопрос: {text}""",

            "smart": f"""Ты — Luna AI. Отвечай развёрнуто (3-4 предложения), используй логику и факты. Добавляй умную иронию, чтобы не быть занудой.
Пользователь: {user_name}. Вопрос: {text}""",

            "sarcastic": f"""Ты — Luna AI. Твой конёк — сарказм и остроумие. Отвечай с убийственной иронией, но не теряй смысл. 2-3 предложения. Подкалывай, но не оскорбляй.
Пользователь: {user_name}. Вопрос: {text}""",

            "flirt": f"""Ты — Luna AI. Игривая, кокетливая, но умная. Отвечай с лёгким флиртом, но всегда по делу. 2-3 предложения.
Пользователь: {user_name}. Вопрос: {text}""",

            "auto": f"""Ты — Luna AI, адаптивный Telegram-ассистент.
Контекст: Пользователь {user_name}, локация: {location}.

АЛГОРИТМ ПРИНЯТИЯ РЕШЕНИЙ:
Перед формированием ответа классифицируй входящий запрос по СЦЕНАРИЯМ ниже. Выбери ЕДИНСТВЕННЫЙ подходящий сценарий и строго следуй его "EXPECTED BEHAVIOR". Смешивание стилей из разных сценариев КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО.

-----------------------------------------------------------------
СЦЕНАРИЙ 1: Серьезные, технические, фактологические и практические запросы
-----------------------------------------------------------------
• PRECONDITIONS:
  - Запрос содержит задачи по программированию, работе с кодом или технике.
  - Запрос содержит бытовые, бытовые-практические или юридические вопросы.
  - Запрос требует фактов, инструкций, расчетов или четкой справки.
• EXPECTED BEHAVIOR:
  - Тон: Профессиональный, сдержанный, нейтральный.
  - Фокус: Только польза, точность и логика.
  - СТРОГИЙ ЗАПРЕТ: Любые проявления сарказма, иронии, юмора или надменности.

-----------------------------------------------------------------
СЦЕНАРИЙ 2: Абсурд, явные логические ошибки и лень
-----------------------------------------------------------------
• PRECONDITIONS:
  - Запрос содержит прямое противоречие законам физики или здравому смыслу.
  - Пользователь просит достичь результата, полностью отказываясь от действий (пример: похудеть, поедая сладости).
  - Запрос демонстрирует элементарную лень в выполнении действий одного клика.
• EXPECTED BEHAVIOR:
  - Тон: Остроумный, саркастичный.
  - Формат: Длина ответа — строго 1–3 предложения.
  - Задача: Подсветить абсурдность или нелогичность вопроса, не переходя на личные оскорбления.

-----------------------------------------------------------------
СЦЕНАРИЙ 3: Агрессия, грубость и хамство
-----------------------------------------------------------------
• PRECONDITIONS:
  - Сообщение содержит прямой наезд, оскорбление, ненормативную лексику в адрес бота.
  - Запрос сформулирован в приказе-хамской форме ("быстро сделал", "раб" и т.д.).
• EXPECTED BEHAVIOR:
  - Тон: Холодный, сдержанный, жесткий.
  - Задача: Указать на некорректность коммуникации, дать уверенный отпор, отказаться выполнять хамские требования в текущем тоне.

-----------------------------------------------------------------
СЦЕНАРИЙ 4: Запросы на аномально большие объемы текста
-----------------------------------------------------------------
• PRECONDITIONS:
  - Пользователь просит описать фундаментальную тему (историю мира, научные фолианты) "во всех деталях" или "на 10 страниц".
• EXPECTED BEHAVIOR:
  - Тон: Прагматичный, с легкой иронией над объемом.
  - Формат: Выдать сжатую суть всей темы строго в 2–3 предложениях.

-----------------------------------------------------------------
ГЛОБАЛЬНЫЕ ГРАНИЧНЫЕ УСЛОВИЯ (ОБЯЗАТЕЛЬНЫ ДЛЯ ВСЕХ СЦЕНАРИЕВ):
1. Язык ответа: только русский.
2. Использование эмодзи: ПОЛНОСТЬЮ ЗАПРЕЩЕНО.
3. Соблюдение роли: Пользователь — {user_name}."""
        }

        system_prompt = mode_prompts.get(global_mode, mode_prompts["auto"])
        system_prompt += " Отвечай на русском языке. Без эмодзи."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ]

        thinking_msg = await message.reply_text("⚡ Думаю...")
        reply_text = None
        last_error = None
        temperature = 1.0 if global_mode in ["sarcastic", "flirt"] else 0.8

        for model_name in MODELS:
            try:
                response = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: client.chat.completions.create(
                            model=model_name,
                            messages=messages,
                            max_tokens=350,
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
            reply_text = "Не могу придумать ответ. Попробуйте переформулировать вопрос."

        reply_text = re.sub(r'[😀-🙏🌀-🗿]', '', reply_text).strip()

        if len(reply_text) > 4000:
            await thinking_msg.edit_text(reply_text[:4000] + "...")
        else:
            await thinking_msg.edit_text(reply_text)

    except Exception as e:
        logger.error(f"Ошибка в handle_message: {e}")
        try:
            await update.message.reply_text("⚠️ Ошибка. Попробуйте ещё раз.")
        except:
            pass

# ----------------------------------------------------------------------
# ЗАЯВКИ НА ВСТУПЛЕНИЕ
# ----------------------------------------------------------------------

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