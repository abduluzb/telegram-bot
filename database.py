# database.py - использует переменные Railway для MySQL

import os
import logging
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, DateTime, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

logger = logging.getLogger(__name__)

# По умолчанию используем SQLite для локальной разработки,
# но если есть переменная USE_SQLITE=False – переключаемся на MySQL
USE_SQLITE = os.getenv("USE_SQLITE", "True").lower() == "true"

if USE_SQLITE:
    DATABASE_URL = "sqlite:///luna_bot.db"
    logger.info("Используется SQLite (локальная БД)")
else:
    # Читаем переменные Railway
    DB_HOST = os.getenv("MYSQLHOST")
    DB_PORT = os.getenv("MYSQLPORT", "3306")
    DB_USER = os.getenv("MYSQLUSER")
    DB_PASSWORD = os.getenv("MYSQLPASSWORD")
    DB_NAME = os.getenv("MYSQLDATABASE")

    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("❌ Не найдены переменные MYSQLHOST, MYSQLUSER, MYSQLPASSWORD, MYSQLDATABASE")

    DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    logger.info(f"Используется MySQL (хост: {DB_HOST})")

# Создаём движок
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine)
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

# Создаём таблицы, если их нет
Base.metadata.create_all(bind=engine)

# ============== ФУНКЦИИ ДЛЯ РАБОТЫ С БД ==============

def get_session():
    return SessionLocal()

# ----- Статистика пользователей -----
def update_user_stats(user_id, text, username=None, first_name=None):
    session = get_session()
    try:
        user = session.query(UserStats).filter_by(user_id=user_id).first()
        if not user:
            user = UserStats(user_id=user_id, username=username, first_name=first_name)
            session.add(user)
        else:
            if username:
                user.username = username
            if first_name:
                user.first_name = first_name
        old_total = user.messages_count * user.avg_len
        user.messages_count += 1
        user.avg_len = (old_total + len(text)) / user.messages_count
        user.last_seen = datetime.utcnow()
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка обновления статистики: {e}")
    finally:
        session.close()

def get_user_stats(user_id):
    session = get_session()
    try:
        user = session.query(UserStats).filter_by(user_id=user_id).first()
        if user:
            return {
                "messages_count": user.messages_count,
                "avg_len": user.avg_len,
                "last_seen": user.last_seen
            }
        return None
    finally:
        session.close()

# ----- Память чата -----
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
        count = session.query(ChatMemory).filter_by(chat_id=chat_id).count()
        if count > 50:
            old = session.query(ChatMemory).filter_by(chat_id=chat_id).order_by(ChatMemory.timestamp).limit(count - 50).all()
            for item in old:
                session.delete(item)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка добавления в память чата: {e}")
    finally:
        session.close()

def get_chat_memory(chat_id, limit=10):
    session = get_session()
    try:
        records = session.query(ChatMemory).filter_by(chat_id=chat_id).order_by(ChatMemory.timestamp.desc()).limit(limit).all()
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
        logger.error(f"Ошибка очистки памяти: {e}")
    finally:
        session.close()

# ----- Нарушения -----
def get_violations(user_id):
    session = get_session()
    try:
        viol = session.query(Violation).filter_by(user_id=user_id).first()
        if viol:
            return {"count": viol.count, "ban_until": viol.ban_until}
        return None
    finally:
        session.close()

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
        logger.error(f"Ошибка обновления нарушений: {e}")
    finally:
        session.close()

def clear_violation(user_id):
    session = get_session()
    try:
        session.query(Violation).filter_by(user_id=user_id).delete()
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка очистки нарушений: {e}")
    finally:
        session.close()

# ----- Напоминания -----
def add_reminder(user_id, chat_id, text, timestamp):
    session = get_session()
    try:
        rem = Reminder(user_id=user_id, chat_id=chat_id, text=text, timestamp=timestamp)
        session.add(rem)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка добавления напоминания: {e}")
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
        logger.error(f"Ошибка удаления напоминания: {e}")
    finally:
        session.close()

# ============== ИНИЦИАЛИЗАЦИЯ ==============
def init_db():
    # Таблицы уже созданы
    logger.info("✅ База данных инициализирована")