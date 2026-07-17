import json
import os
from datetime import datetime

# Читаем ваш JSON-файл
with open('www_youtube_com_cookies.json', 'r') as f:
    cookies = json.load(f)

# Открываем файл для записи в Netscape-формате
with open('cookies.txt', 'w') as f:
    # Заголовок Netscape
    f.write("# Netscape HTTP Cookie File\n")
    
    for c in cookies:
        domain = c.get('domain', '')
        # Если domain начинается с точки, оставляем как есть, иначе добавляем точку
        if not domain.startswith('.'):
            domain = '.' + domain
        flag = 'TRUE' if not c.get('hostOnly', False) else 'FALSE'
        path = c.get('path', '/')
        secure = 'TRUE' if c.get('secure', False) else 'FALSE'
        expires = str(int(c.get('expirationDate', 0))) if c.get('expirationDate') else '0'
        name = c.get('name', '')
        value = c.get('value', '')
        # Формат: domain, flag, path, secure, expires, name, value
        f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")

print("✅ Файл cookies.txt успешно создан!")