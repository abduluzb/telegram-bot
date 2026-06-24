# database.py - работа с SQLite

import sqlite3
from typing import Dict, Optional

DB_PATH = "bot_data.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Глобальные настройки (режим)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS global_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # Если нет режима, ставим fast
    cursor.execute("INSERT OR IGNORE INTO global_settings (key, value) VALUES ('mode', 'fast')")

    # Статистика пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            avg_len REAL DEFAULT 0,
            msg_count INTEGER DEFAULT 0,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

# ========== ГЛОБАЛЬНЫЙ РЕЖИМ ==========
def get_global_mode() -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM global_settings WHERE key = 'mode'")
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else "fast"

def set_global_mode(mode: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE global_settings SET value = ? WHERE key = 'mode'", (mode,))
    # Если обновление не затронуло ни одной строки (записи нет), вставляем
    if cursor.rowcount == 0:
        cursor.execute("INSERT INTO global_settings (key, value) VALUES ('mode', ?)", (mode,))
    conn.commit()
    conn.close()

# ========== СТАТИСТИКА ПОЛЬЗОВАТЕЛЕЙ ==========
def update_user_stats(user_id: int, text: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT avg_len, msg_count FROM user_stats WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        avg_len = row["avg_len"]
        msg_count = row["msg_count"] + 1
        new_avg = (avg_len * (msg_count - 1) + len(text)) / msg_count
        cursor.execute(
            "UPDATE user_stats SET avg_len = ?, msg_count = ?, last_seen = CURRENT_TIMESTAMP WHERE user_id = ?",
            (new_avg, msg_count, user_id)
        )
    else:
        cursor.execute(
            "INSERT INTO user_stats (user_id, avg_len, msg_count) VALUES (?, ?, ?)",
            (user_id, len(text), 1)
        )
    conn.commit()
    conn.close()

def get_user_stats(user_id: int) -> Optional[Dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT avg_len, msg_count FROM user_stats WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None