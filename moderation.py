# moderation.py
import time
from datetime import datetime
from typing import Dict
from telegram import Update, Chat
from telegram.ext import ContextTypes

from config import BAD_WORDS, AUTO_MODERATION_ENABLED, logger
from utils import is_owner, notify_owner, format_time

user_violations: Dict[int, Dict] = {}

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