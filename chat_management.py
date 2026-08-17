# chat_management.py
from telegram import Update, Chat, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import disabled_chats, OWNER_NAME, OWNER_USER_ID, logger
from utils import is_owner, chat_members

async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Только владелец.")
        return
    chat_ids = list(chat_members.keys())
    if not chat_ids:
        await update.message.reply_text("📭 Нет чатов, где есть бот.")
        return

    keyboard = []
    for cid in chat_ids:
        try:
            chat = await context.bot.get_chat(cid)
            if chat.type == Chat.PRIVATE:
                title = chat.first_name or chat.username or f"Пользователь {cid}"
            else:
                title = chat.title or f"Чат {cid}"
        except Exception:
            title = f"Чат {cid}"
        status = "🔴 Отключена" if cid in disabled_chats else "🟢 Активна"
        keyboard.append([InlineKeyboardButton(f"{title} — {status}", callback_data=f"chat_manage_{cid}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])

    await update.message.reply_text(
        "📋 **Список чатов с Luna AI:**\nВыберите чат для управления.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def manage_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await query.edit_message_text("⛔ Доступ запрещён.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]))
        return

    chat_ids = list(chat_members.keys())
    if not chat_ids:
        await query.edit_message_text("📭 Нет чатов, где есть бот.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
        return

    keyboard = []
    for cid in chat_ids:
        try:
            chat = await context.bot.get_chat(cid)
            if chat.type == Chat.PRIVATE:
                title = chat.first_name or chat.username or f"Пользователь {cid}"
            else:
                title = chat.title or f"Чат {cid}"
        except Exception:
            title = f"Чат {cid}"
        status = "🔴 Отключена" if cid in disabled_chats else "🟢 Активна"
        keyboard.append([InlineKeyboardButton(f"{title} — {status}", callback_data=f"chat_manage_{cid}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")])

    await query.edit_message_text(
        "📋 **Список чатов с Luna AI:**\nВыберите чат для управления.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    await query.answer()

async def chat_manage(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    try:
        chat = await context.bot.get_chat(chat_id)
        if chat.type == Chat.PRIVATE:
            title = chat.first_name or chat.username or f"Пользователь {chat_id}"
        else:
            title = chat.title or f"Чат {chat_id}"
    except Exception:
        title = f"Чат {chat_id}"

    is_disabled = chat_id in disabled_chats
    status_text = "🔴 Отключена" if is_disabled else "🟢 Активна"

    keyboard = []
    if is_disabled:
        keyboard.append([InlineKeyboardButton("✅ Включить бота", callback_data=f"chat_enable_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("❌ Отключить бота", callback_data=f"chat_disable_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад к списку", callback_data="manage_chats")])
    keyboard.append([InlineKeyboardButton("🔙 В админ-панель", callback_data="admin_panel")])

    await query.edit_message_text(
        f"📌 **Чат:** {title}\n"
        f"🆔 ID: `{chat_id}`\n"
        f"📊 Статус: {status_text}\n\n"
        f"Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    await query.answer()

async def chat_disable(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    if chat_id in disabled_chats:
        await query.answer("Бот уже отключён в этом чате.")
        return

    disabled_chats.add(chat_id)
    logger.info(f"Бот отключён в чате {chat_id}")

    owner_mention = f"@{OWNER_NAME}" if OWNER_NAME and OWNER_NAME.startswith('@') else f"@{OWNER_NAME}" if OWNER_NAME else f"ID: {OWNER_USER_ID}"
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Внимание! Владелец бота ({owner_mention}) отключил возможности Luna AI в этом чате.\n\n"
                 f"Для дополнительной информации напишите владельцу."
        )
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление в чат {chat_id}: {e}")

    await query.answer("Бот отключён в этом чате.")
    await chat_manage(update, context, chat_id)

async def chat_enable(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    if chat_id not in disabled_chats:
        await query.answer("Бот уже включён в этом чате.")
        return

    disabled_chats.discard(chat_id)
    logger.info(f"Бот включён в чате {chat_id}")

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Luna AI снова активна в этом чате! Все возможности доступны."
        )
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление в чат {chat_id}: {e}")

    await query.answer("Бот включён в этом чате.")
    await chat_manage(update, context, chat_id)