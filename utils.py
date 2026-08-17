# utils.py
import os
import time
import re
import shutil
import asyncio
from typing import Dict, List, Set, Optional, Tuple
from datetime import datetime, timedelta
import pytz
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from config import OWNER_USER_ID, MAX_MEMORY, logger

# Хранилища
chat_members: Dict[int, Set[int]] = {}
user_names: Dict[int, str] = {}
last_request_time: Dict[int, float] = {}
user_memory: Dict[int, List[Dict]] = {}

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

def is_owner(user_id: int) -> bool:
    if OWNER_USER_ID is None:
        return True
    return user_id == OWNER_USER_ID

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
        from database import clear_chat_memory
        clear_chat_memory(chat_id)

async def notify_owner(context, text: str):
    if OWNER_USER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_USER_ID, text=text)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление владельцу: {e}")

def get_ffmpeg_path() -> Optional[str]:
    path = shutil.which('ffmpeg')
    if path:
        return path
    standard_paths = ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']
    for p in standard_paths:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return None

def format_time(seconds: int) -> str:
    if seconds < 60:
        return f"⏱️ {seconds} секунд"
    elif seconds < 3600:
        return f"⏱️ {seconds//60} минут"
    elif seconds < 86400:
        return f"⏱️ {seconds//3600} часов"
    else:
        return f"⏱️ {seconds//86400} дней"

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

async def check_reminders(application):
    from database import get_due_reminders, delete_reminder
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

async def get_wikipedia_summary(query: str, lang: str = "ru") -> Optional[str]:
    import aiohttp
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

def search_github_code(query: str) -> Optional[List[Dict]]:
    from config import GITHUB_TOKEN, GITHUB_REPO
    import requests
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
            logger.error(f"GitHub API error: {response.status_code} - {response.text}")
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

def get_github_file_content(file_path: str) -> Optional[str]:
    from config import GITHUB_TOKEN, GITHUB_REPO
    import requests, base64
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            logger.error(f"GitHub API error: {response.status_code} - {response.text}")
            return None
        data = response.json()
        content = data.get("content", "")
        if content:
            decoded = base64.b64decode(content).decode("utf-8")
            return decoded
        return None
    except Exception as e:
        logger.error(f"Ошибка получения файла: {e}")
        return None