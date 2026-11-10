import os
import json
import urllib.request
from dotenv import load_dotenv

# Принудительно читаем свежий ключ из .env
load_dotenv(override=True)
api_key = os.getenv("GEMINI_API_KEY")
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"

try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode('utf-8'))
        print("\n✅ Разрешенные модели для твоего ключа:")
        for m in data.get("models", []):
            name = m.get("name", "").replace("models/", "")
            methods = m.get("supportedGenerationMethods", [])
            
            # Фильтруем только модели, умеющие генерировать контент и анализировать фото
            if "generateContent" in methods and ("flash" in name or "pro" in name):
                print(f" -> {name}")
except Exception as e:
    print(f"Ошибка при запросе: {e}")