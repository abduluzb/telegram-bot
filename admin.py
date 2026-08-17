# admin.py
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import OWNER_USER_ID, logger, pending_requests
from utils import is_owner, chat_members, notify_owner
from database import clear_table, get_session, UserStats, UserInfo, ChatMemory, Violation, Reminder, Note, Config, TrainingData, DeletedMessage, DailyStats, ReactionLog

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
        return

    context.user_data['admin_text'] = None
    context.user_data['admin_photo'] = None
    context.user_data['admin_photo_file_id'] = None
    context.user_data['admin_waiting'] = None

    text = (
        "👑 Админ-панель Luna AI\n\n"
        "Здесь вы можете подготовить рассылку для всех чатов.\n"
        "1️⃣ Напишите текст (нажмите кнопку)\n"
        "2️⃣ Прикрепите фото (опционально)\n"
        "3️⃣ Отправьте рассылку\n\n"
        "Текущий статус:"
    )
    status = "📝 Текст: не задан\n🖼️ Фото: нет"
    msg = await update.effective_message.reply_text(
        text + "\n\n" + status,
        reply_markup=get_admin_keyboard(False, False),
        parse_mode=None
    )
    context.user_data['admin_panel_message_id'] = msg.message_id

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await query.edit_message_text("⛔ Доступ запрещён.", parse_mode=None)
        return

    data = query.data
    user_data = context.user_data
    panel_id = user_data.get('admin_panel_message_id')

    if data == "admin_write_text":
        user_data['admin_waiting'] = 'text'
        await query.edit_message_text(
            "✏️ Введите текст рассылки\n\n"
            "Просто напишите сообщение в этот чат. Я сохраню его.\n"
            "Чтобы отменить, нажмите /cancel_admin",
            parse_mode=None
        )
        await query.message.delete()

    elif data == "admin_add_photo":
        user_data['admin_waiting'] = 'photo'
        await query.edit_message_text(
            "🖼️ Прикрепите фото\n\n"
            "Отправьте мне фото (одно). Я сохраню его.\n"
            "Чтобы пропустить, нажмите /skip_photo",
            parse_mode=None
        )
        await query.message.delete()

    elif data == "admin_clear":
        user_data['admin_text'] = None
        user_data['admin_photo'] = None
        user_data['admin_photo_file_id'] = None
        user_data['admin_waiting'] = None
        if panel_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=panel_id,
                    text="🗑️ Все данные очищены.\n\nВозвращаюсь в панель.",
                    reply_markup=get_admin_keyboard(False, False),
                    parse_mode=None
                )
                await query.delete_message()
                return
            except:
                pass
        await query.edit_message_text(
            "🗑️ Все данные очищены.",
            reply_markup=get_admin_keyboard(False, False),
            parse_mode=None
        )

    elif data == "admin_preview":
        text = user_data.get('admin_text', '')
        photo = user_data.get('admin_photo_file_id')
        if not text and not photo:
            await query.edit_message_text(
                "❌ Нет данных для предпросмотра.\nЗадайте текст или добавьте фото.",
                reply_markup=get_admin_keyboard(False, False),
                parse_mode=None
            )
            return
        preview_text = "👀 Предпросмотр рассылки:\n\n"
        if text:
            preview_text += f"Текст:\n{text}\n\n"
        if photo:
            preview_text += "Фото: прикреплено"
        if photo:
            await query.message.reply_photo(
                photo=photo,
                caption=preview_text,
                parse_mode=None
            )
        else:
            await query.message.reply_text(preview_text, parse_mode=None)
        await query.answer()

    elif data == "admin_send":
        text = user_data.get('admin_text', '')
        photo = user_data.get('admin_photo_file_id')
        if not text and not photo:
            await query.edit_message_text(
                "❌ Нет данных для отправки.",
                reply_markup=get_admin_keyboard(False, False),
                parse_mode=None
            )
            return

        all_chats = list(chat_members.keys())
        if not all_chats:
            await query.edit_message_text(
                "📭 Нет известных чатов.",
                reply_markup=get_admin_keyboard(False, False),
                parse_mode=None
            )
            return

        status_msg = await query.edit_message_text(
            f"⏳ Отправляю рассылку в {len(all_chats)} чатов...",
            parse_mode=None
        )
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
                        photo=photo,
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
            f"❌ Ошибок: {errors}",
            reply_markup=get_admin_keyboard(False, False),
            parse_mode=None
        )
        user_data['admin_text'] = None
        user_data['admin_photo'] = None
        user_data['admin_photo_file_id'] = None

    elif data == "admin_close":
        user_data['admin_text'] = None
        user_data['admin_photo'] = None
        user_data['admin_photo_file_id'] = None
        user_data['admin_waiting'] = None
        await query.edit_message_text("🔙 Панель закрыта.", parse_mode=None)
        if panel_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=panel_id
                )
            except:
                pass

    elif data in ("admin_text_set", "admin_photo_set"):
        await query.answer("Уже задано")

async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if context.user_data.get('admin_waiting') != 'text':
        # Делегируем в основной обработчик
        from handlers import handle_message
        await handle_message(update, context)
        return

    if not is_owner(user_id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    text = update.message.text
    if text.startswith('/'):
        await update.message.reply_text("❌ Команды не принимаются. Напишите текст.")
        return

    context.user_data['admin_text'] = text
    context.user_data['admin_waiting'] = None
    logger.info(f"✅ Текст сохранён: {text[:100]}...")

    panel_id = context.user_data.get('admin_panel_message_id')
    if panel_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=panel_id,
                text=f"✅ Текст сохранён:\n\n{text[:200]}{'...' if len(text)>200 else ''}\n\nВозвращаюсь в панель.",
                reply_markup=get_admin_keyboard(True, bool(context.user_data.get('admin_photo_file_id'))),
                parse_mode=None
            )
            await update.message.delete()
            return
        except Exception as e:
            logger.error(f"Ошибка редактирования панели: {e}")
            await update.message.reply_text(
                f"✅ Текст сохранён:\n\n{text[:200]}{'...' if len(text)>200 else ''}",
                reply_markup=get_admin_keyboard(True, bool(context.user_data.get('admin_photo_file_id'))),
                parse_mode=None
            )
    else:
        await update.message.reply_text(
            f"✅ Текст сохранён:\n\n{text[:200]}{'...' if len(text)>200 else ''}",
            reply_markup=get_admin_keyboard(True, bool(context.user_data.get('admin_photo_file_id'))),
            parse_mode=None
        )

async def handle_admin_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if context.user_data.get('admin_waiting') != 'photo':
        return

    if not is_owner(user_id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    photo = update.message.photo
    if not photo:
        await update.message.reply_text("❌ Отправьте фото (не документ). Попробуйте снова.")
        return

    photo_file = photo[-1]
    context.user_data['admin_photo_file_id'] = photo_file.file_id
    context.user_data['admin_photo'] = photo_file
    context.user_data['admin_waiting'] = None
    logger.info("✅ Фото сохранено")

    panel_id = context.user_data.get('admin_panel_message_id')
    if panel_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=panel_id,
                text="✅ Фото сохранено.\n\nВозвращаюсь в панель.",
                reply_markup=get_admin_keyboard(bool(context.user_data.get('admin_text')), True),
                parse_mode=None
            )
            await update.message.delete()
            return
        except Exception as e:
            logger.error(f"Ошибка редактирования панели: {e}")
            await update.message.reply_text(
                "✅ Фото сохранено.",
                reply_markup=get_admin_keyboard(bool(context.user_data.get('admin_text')), True),
                parse_mode=None
            )
    else:
        await update.message.reply_text(
            "✅ Фото сохранено.",
            reply_markup=get_admin_keyboard(bool(context.user_data.get('admin_text')), True),
            parse_mode=None
        )

async def cancel_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    context.user_data['admin_waiting'] = None
    panel_id = context.user_data.get('admin_panel_message_id')
    if panel_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=panel_id,
                text="❌ Отменено. Возвращаюсь в панель.",
                reply_markup=get_admin_keyboard(
                    bool(context.user_data.get('admin_text')),
                    bool(context.user_data.get('admin_photo_file_id'))
                ),
                parse_mode=None
            )
            await update.message.delete()
            return
        except:
            pass
    await update.message.reply_text(
        "❌ Отменено.",
        reply_markup=get_admin_keyboard(
            bool(context.user_data.get('admin_text')),
            bool(context.user_data.get('admin_photo_file_id'))
        ),
        parse_mode=None
    )

async def skip_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    if context.user_data.get('admin_waiting') != 'photo':
        return

    context.user_data['admin_waiting'] = None
    panel_id = context.user_data.get('admin_panel_message_id')
    if panel_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=panel_id,
                text="⏭️ Фото пропущено. Возвращаюсь в панель.",
                reply_markup=get_admin_keyboard(bool(context.user_data.get('admin_text')), False),
                parse_mode=None
            )
            await update.message.delete()
            return
        except:
            pass
    await update.message.reply_text(
        "⏭️ Фото пропущено.",
        reply_markup=get_admin_keyboard(bool(context.user_data.get('admin_text')), False),
        parse_mode=None
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update.effective_user.id):
        await query.edit_message_text("⛔ Доступ запрещён.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]))
        return
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск в коде", callback_data="search_code")],
        [InlineKeyboardButton("🧹 Очистить таблицу", callback_data="clear_table_menu")],
        [InlineKeyboardButton("📊 Статистика БД", callback_data="db_stats")],
        [InlineKeyboardButton("📋 Управление чатами", callback_data="manage_chats")],
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
        "Используйте команду: `луна искать в коде <текст>`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )