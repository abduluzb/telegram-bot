# api_clients.py
import openai
from config import OPENROUTER_API_KEY, logger

ai_clients = []

if OPENROUTER_API_KEY:
    client = openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    # Выбери модель. Можно поменять на любую из списка:
    # https://openrouter.ai/models
    ai_clients.append({
        "name": "OpenRouter (Claude 3.5 Sonnet)",
        "client": client,
        "model": "anthropic/claude-3.5-sonnet",
        "max_tokens": 500,
        "temperature": 0.8,
    })
    # Можно добавить второй вариант для fallback внутри OpenRouter (например, GPT-4o)
    ai_clients.append({
        "name": "OpenRouter (GPT-4o)",
        "client": client,
        "model": "openai/gpt-4o",
        "max_tokens": 500,
        "temperature": 0.8,
    })
    logger.info("✅ OpenRouter подключен")
else:
    logger.warning("⚠️ OPENROUTER_API_KEY не задан! Бот не сможет отвечать.")