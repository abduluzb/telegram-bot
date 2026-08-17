import asyncio
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatJoinRequestHandler, filters

from config import TELEGRAM_TOKEN, OWNER_USER_ID, OWNER_NAME, logger, SHAZAM_API_KEY
from database import init_db, get_all_chat_ids
from utils import check_reminders, chat_members
from handlers import (
    start_command, help_command, setmode_command, getmode_command,
    set_moderation_command, broadcast_command, stats_detail_command,
    setcity_command, settimezone_command, notes_command, delnote_command,
    weather_command, imagine_command, yt_command, remind_command,
    members_command, reset_command, stats_command, warn_command,
    unban_command, wiki_command, owners_command, button_callback,
    handle_message, join_request_callback
)
from music_trailer import trailer_command, trailer_select_callback, music_command, music_yt_select_callback, music_cancel_callback
from admin import admin_panel_start, admin_callback, handle_admin_text_input, handle_admin_photo_input, cancel_admin_input, skip_photo_input
from instagram import instagram_audio_callback, instagram_find_full_callback
from chat_management import groups_command
from api_clients import youtube, spotify

def main():
    init_db()
    logger.info("▶️ Инициализация приложения Luna AI...")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # === Команды ===
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
    application.add_handler(CommandHandler("groups", groups_command))

    # === Админ-панель ===
    application.add_handler(CommandHandler("admin", admin_panel_start))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text_input))
    application.add_handler(MessageHandler(filters.PHOTO, handle_admin_photo_input))
    application.add_handler(CommandHandler("cancel_admin", cancel_admin_input))
    application.add_handler(CommandHandler("skip_photo", skip_photo_input))

    # === Специфичные callback-обработчики ===
    application.add_handler(CallbackQueryHandler(trailer_select_callback, pattern="^trailer_select_"))
    application.add_handler(CallbackQueryHandler(music_yt_select_callback, pattern="^music_yt_select_"))
    application.add_handler(CallbackQueryHandler(music_cancel_callback, pattern="^music_cancel$"))
    application.add_handler(CallbackQueryHandler(instagram_audio_callback, pattern="^ig_audio_"))
    application.add_handler(CallbackQueryHandler(instagram_find_full_callback, pattern="^ig_find_full_"))

    # === Общий callback-обработчик ===
    application.add_handler(CallbackQueryHandler(button_callback))

    # === Обработчик входящих сообщений ===
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # === Запросы на вступление ===
    application.add_handler(ChatJoinRequestHandler(join_request_callback))

    async def post_init(app):
        global OWNER_NAME
        if OWNER_USER_ID:
            try:
                chat = await app.bot.get_chat(OWNER_USER_ID)
                if chat.username:
                    OWNER_NAME = f"@{chat.username}"
                else:
                    OWNER_NAME = chat.first_name or str(OWNER_USER_ID)
            except Exception as e:
                logger.warning(f"Не удалось получить имя владельца: {e}")
                OWNER_NAME = f"ID: {OWNER_USER_ID}"
        else:
            OWNER_NAME = None

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
            BotCommand("music", "Поиск музыки (аудио)"),
            BotCommand("trailer", "Поиск трейлеров (MP4)"),
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
            BotCommand("groups", "Управление чатами (владелец)"),
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

    logger.info("🚀 Luna AI запущен с трейлерами (MP4), музыкой, Instagram видео и Shazam!")
    logger.info("💬 Глобальный режим: fast/smart/sarcastic/flirt/auto")
    logger.info("🎵 Spotify: подключен" if spotify else "🎵 Spotify: не подключен")
    logger.info("🎬 YouTube: подключен" if youtube else "🎬 YouTube: не подключен")
    logger.info("📥 Instagram: видео + кнопки аудио и полной версии через Shazam")
    logger.info("🎵 Shazam API: " + ("подключен" if SHAZAM_API_KEY else "НЕ НАСТРОЕН!"))

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()