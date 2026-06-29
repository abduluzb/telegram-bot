# database.py - без таблицы user_interests

import os
import logging
import time
import re
from typing import List, Optional, Dict, Any
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Float,
    DateTime, BigInteger, Boolean, ForeignKey, text
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.exc import OperationalError, InterfaceError
from datetime import datetime

logger = logging.getLogger(__name__)

# === Определяем, какую БД использовать ===
USE_SQLITE = os.getenv("USE_SQLITE", "True").lower() == "true"

if USE_SQLITE:
    DATABASE_URL = "sqlite:///luna_bot.db"
    logger.info("Используется SQLite (локальная БД)")
else:
    db_url = os.getenv("DATABASE_URL") or os.getenv("MYSQL_URL")
    if db_url:
        if db_url.startswith("mysql://") and "+pymysql" not in db_url:
            db_url = db_url.replace("mysql://", "mysql+pymysql://", 1)
        DATABASE_URL = db_url
        logger.info("Используется MySQL по URL из переменной")
    else:
        DB_HOST = os.getenv("MYSQLHOST")
        DB_PORT = os.getenv("MYSQLPORT", "3306")
        DB_USER = os.getenv("MYSQLUSER")
        DB_PASSWORD = os.getenv("MYSQLPASSWORD")
        DB_NAME = os.getenv("MYSQLDATABASE")
        if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
            raise ValueError("❌ Для MySQL не хватает переменных!")
        DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        logger.info(f"Используется MySQL (хост: {DB_HOST})")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    echo=False
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

# ============== МОДЕЛИ ==============

class UserStats(Base):
    __tablename__ = "user_stats"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, unique=True, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    messages_count = Column(Integer, default=0)
    avg_len = Column(Float, default=0.0)
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserInfo(Base):
    __tablename__ = "user_info"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, unique=True, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    language_code = Column(String(10), nullable=True)
    timezone = Column(String(50), nullable=True)
    city = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ChatMemory(Base):
    __tablename__ = "chat_memory"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, index=True)
    user_id = Column(BigInteger, index=True)
    user_name = Column(String(255))
    text = Column(Text)
    role = Column(String(20), default="user")
    timestamp = Column(DateTime, default=datetime.utcnow)

class Violation(Base):
    __tablename__ = "violations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    chat_id = Column(BigInteger)
    count = Column(Integer, default=0)
    ban_until = Column(DateTime, nullable=True)
    last_violation = Column(DateTime, default=datetime.utcnow)

class Reminder(Base):
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    chat_id = Column(BigInteger)
    text = Column(Text)
    timestamp = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Config(Base):
    __tablename__ = "config"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), unique=True, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ============== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==============

def get_session():
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except (OperationalError, InterfaceError) as e:
        logger.warning(f"Соединение потеряно, переподключаемся: {e}")
        session.rollback()
        session.close()
        session = SessionLocal()
    return session

def retry_on_disconnect(func):
    def wrapper(*args, **kwargs):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (OperationalError, InterfaceError) as e:
                if attempt == max_retries - 1:
                    logger.error(f"Ошибка после {max_retries} попыток: {e}")
                    raise
                logger.warning(f"Ошибка соединения, попытка {attempt+1}: {e}")
                time.sleep(1)
                continue
    return wrapper

# ============== ГЛОБАЛЬНЫЙ РЕЖИМ ==============

def get_global_mode(default="fast") -> str:
    session = get_session()
    try:
        config = session.query(Config).filter_by(key="global_mode").first()
        return config.value if config and config.value else default
    except Exception as e:
        logger.error(f"Ошибка получения глобального режима: {e}")
        return default
    finally:
        session.close()

def set_global_mode(mode: str) -> None:
    valid = ["fast", "smart", "sarcastic", "flirt"]
    if mode not in valid:
        raise ValueError(f"Некорректный режим: {mode}")
    session = get_session()
    try:
        config = session.query(Config).filter_by(key="global_mode").first()
        if config:
            config.value = mode
            config.updated_at = datetime.utcnow()
        else:
            config = Config(key="global_mode", value=mode)
            session.add(config)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка установки глобального режима: {e}")
        raise
    finally:
        session.close()

# ============== ПОЛЬЗОВАТЕЛЬСКАЯ ИНФОРМАЦИЯ ==============

@retry_on_disconnect
def get_or_create_user_info(user_id, username=None, first_name=None, last_name=None, language_code=None):
    session = get_session()
    try:
        user_info = session.query(UserInfo).filter_by(user_id=user_id).first()
        if not user_info:
            user_info = UserInfo(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
            )
            session.add(user_info)
            session.commit()
        else:
            updated = False
            if username and user_info.username != username:
                user_info.username = username; updated = True
            if first_name and user_info.first_name != first_name:
                user_info.first_name = first_name; updated = True
            if last_name and user_info.last_name != last_name:
                user_info.last_name = last_name; updated = True
            if language_code and user_info.language_code != language_code:
                user_info.language_code = language_code; updated = True
            if updated:
                user_info.updated_at = datetime.utcnow()
                session.commit()
        return {
            "user_id": user_info.user_id,
            "username": user_info.username,
            "first_name": user_info.first_name,
            "last_name": user_info.last_name,
            "language_code": user_info.language_code,
            "timezone": user_info.timezone,
            "city": user_info.city,
        }
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка в get_or_create_user_info: {e}")
        return None
    finally:
        session.close()

@retry_on_disconnect
def update_user_city_timezone(user_id, city=None, timezone=None):
    session = get_session()
    try:
        user_info = session.query(UserInfo).filter_by(user_id=user_id).first()
        if not user_info:
            return False
        if city:
            user_info.city = city
        if timezone:
            user_info.timezone = timezone
        user_info.updated_at = datetime.utcnow()
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка обновления city/timezone: {e}")
        return False
    finally:
        session.close()

# ============== СТАТИСТИКА ==============

@retry_on_disconnect
def update_user_stats(user_id, text, username=None, first_name=None):
    session = get_session()
    try:
        user = session.query(UserStats).filter_by(user_id=user_id).first()
        if not user:
            user = UserStats(user_id=user_id, username=username, first_name=first_name,
                             messages_count=0, avg_len=0.0)
            session.add(user)
        else:
            if username:
                user.username = username
            if first_name:
                user.first_name = first_name
        if user.messages_count is None:
            user.messages_count = 0
        if user.avg_len is None:
            user.avg_len = 0.0
        old_total = user.messages_count * user.avg_len
        user.messages_count += 1
        user.avg_len = (old_total + len(text)) / user.messages_count
        user.last_seen = datetime.utcnow()
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка update_user_stats: {e}")
    finally:
        session.close()

def get_user_stats(user_id):
    session = get_session()
    try:
        user = session.query(UserStats).filter_by(user_id=user_id).first()
        if user:
            return {
                "messages_count": user.messages_count or 0,
                "avg_len": user.avg_len or 0.0,
                "last_seen": user.last_seen
            }
        return None
    finally:
        session.close()

# ============== ПАМЯТЬ ЧАТА И ИСТОРИЯ ПОЛЬЗОВАТЕЛЯ ==============

@retry_on_disconnect
def add_chat_memory(chat_id, user_id, user_name, text, role="user"):
    session = get_session()
    try:
        memory = ChatMemory(
            chat_id=chat_id,
            user_id=user_id,
            user_name=user_name,
            text=text,
            role=role,
            timestamp=datetime.utcnow()
        )
        session.add(memory)
        session.commit()
        count = session.query(ChatMemory).filter_by(user_id=user_id).count()
        if count > 100:
            oldest = session.query(ChatMemory).filter_by(user_id=user_id).order_by(ChatMemory.timestamp.asc()).first()
            if oldest:
                session.delete(oldest)
                session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка add_chat_memory: {e}")
    finally:
        session.close()

def get_chat_memory(chat_id, limit=10):
    session = get_session()
    try:
        records = session.query(ChatMemory).filter_by(chat_id=chat_id).order_by(ChatMemory.timestamp.desc()).limit(limit).all()
        return [{"user_name": r.user_name, "text": r.text, "role": r.role} for r in reversed(records)]
    finally:
        session.close()

def get_user_history(user_id, limit=30):
    session = get_session()
    try:
        records = session.query(ChatMemory).filter_by(user_id=user_id).order_by(ChatMemory.timestamp.desc()).limit(limit).all()
        return [{"user_name": r.user_name, "text": r.text, "role": r.role} for r in reversed(records)]
    finally:
        session.close()

def clear_chat_memory(chat_id):
    session = get_session()
    try:
        session.query(ChatMemory).filter_by(chat_id=chat_id).delete()
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка clear_chat_memory: {e}")
    finally:
        session.close()

# ============== НАРУШЕНИЯ ==============

def get_violations(user_id):
    session = get_session()
    try:
        viol = session.query(Violation).filter_by(user_id=user_id).first()
        return {"count": viol.count, "ban_until": viol.ban_until} if viol else None
    finally:
        session.close()

@retry_on_disconnect
def update_violation(user_id, chat_id, increment=1, ban_until=None):
    session = get_session()
    try:
        viol = session.query(Violation).filter_by(user_id=user_id).first()
        if not viol:
            viol = Violation(user_id=user_id, chat_id=chat_id, count=0)
            session.add(viol)
        viol.count += increment
        if ban_until:
            viol.ban_until = ban_until
        viol.last_violation = datetime.utcnow()
        session.commit()
        return viol.count
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка update_violation: {e}")
    finally:
        session.close()

def clear_violation(user_id):
    session = get_session()
    try:
        session.query(Violation).filter_by(user_id=user_id).delete()
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка clear_violation: {e}")
    finally:
        session.close()

# ============== НАПОМИНАНИЯ ==============

@retry_on_disconnect
def add_reminder(user_id, chat_id, text, timestamp):
    session = get_session()
    try:
        rem = Reminder(user_id=user_id, chat_id=chat_id, text=text, timestamp=timestamp)
        session.add(rem)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка add_reminder: {e}")
    finally:
        session.close()

def get_due_reminders(current_time):
    session = get_session()
    try:
        due = session.query(Reminder).filter(Reminder.timestamp <= current_time).all()
        return [{"id": r.id, "user_id": r.user_id, "chat_id": r.chat_id, "text": r.text} for r in due]
    finally:
        session.close()

def delete_reminder(reminder_id):
    session = get_session()
    try:
        session.query(Reminder).filter_by(id=reminder_id).delete()
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка delete_reminder: {e}")
    finally:
        session.close()

# ============== ЗАМЕТКИ ==============

@retry_on_disconnect
def add_note(user_id, text):
    session = get_session()
    try:
        note = Note(user_id=user_id, text=text)
        session.add(note)
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка add_note: {e}")
        return False
    finally:
        session.close()

def get_notes(user_id, limit=10):
    session = get_session()
    try:
        notes = session.query(Note).filter_by(user_id=user_id).order_by(Note.created_at.desc()).limit(limit).all()
        return [{"id": n.id, "text": n.text, "created_at": n.created_at} for n in notes]
    finally:
        session.close()

def delete_note(note_id):
    session = get_session()
    try:
        session.query(Note).filter_by(id=note_id).delete()
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка delete_note: {e}")
        return False
    finally:
        session.close()

# ============== ОЧИСТКА ТАБЛИЦ ==============

@retry_on_disconnect
def clear_table(table_name: str) -> bool:
    session = get_session()
    try:
        valid = {
            "user_stats": UserStats,
            "user_info": UserInfo,
            "chat_memory": ChatMemory,
            "violations": Violation,
            "reminders": Reminder,
            "notes": Note,
            "config": Config,
        }
        if table_name not in valid:
            logger.warning(f"Попытка очистить недопустимую таблицу: {table_name}")
            return False
        model = valid[table_name]
        deleted = session.query(model).delete()
        session.commit()
        logger.info(f"Очищена таблица {table_name}, удалено {deleted} записей")
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка clear_table: {e}")
        return False
    finally:
        session.close()

# ============== ИНИЦИАЛИЗАЦИЯ ==============

def init_db():
    try:
        session = get_session()
        session.execute(text("SELECT 1"))
        session.close()
        logger.info("✅ База данных инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к базе данных: {e}")
        raise