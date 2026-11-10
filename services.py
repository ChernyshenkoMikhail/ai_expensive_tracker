import os
import json
import asyncio
import aiohttp
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ==========================================
# 1. РАБОТА С ИИ (Анализ картинки с Backoff)
# ==========================================
async def fetch_gemini_with_retry(base64_image: str, mime_type: str, max_retries: int = 3) -> dict:
    """
    Отправляет фото в Gemini 3.8 Flash. 
    Включает защиту от перегрузки (Exponential Backoff).
    """
    api_key = os.getenv('GEMINI_API_KEY')
    
    # 1. Используем стабильную версию API v1
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-3.8-flash:generateContent?key={api_key}"
    
    system_instruction = """
    Ты финансовый аналитик. Проанализируй фото (receipt или screenshot).
    Верни строго JSON.
    Если 'screenshot' (банковская выписка): 
    {"document_type": "screenshot", "transactions": [{"date": "YYYY-MM-DD", "merchant": "Имя", "amount": 100.50, "currency": "RUB", "category": "Категория"}]}
    
    Если 'receipt' (чек из магазина): 
    {"document_type": "receipt", "receipt_info": {"date": "YYYY-MM-DD", "merchant": "Магазин"}, "items": [{"item_name": "Товар", "quantity": 1, "price_per_unit": 80.00, "total_price": 80.00, "category": "Категория"}]}
    """
    
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": [{
            "parts": [
                # 2. Обязательный короткий текст рядом с картинкой
                {"text": "Извлеки данные из этого документа в формате JSON:"},
                {"inlineData": {"mimeType": mime_type, "data": base64_image}}
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    
    headers = {"Content-Type": "application/json"}
    
    for attempt in range(max_retries):
        try:
            # 3. Жесткий контроль того, что мы делаем именно POST-запрос с правильными заголовками
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    
                    if response.status == 200:
                        data = await response.json()
                        ai_text = data['candidates'][0]['content']['parts'][0]['text']
                        return json.loads(ai_text)
                    
                    # Если сервер перегружен (429) или недоступен (503)
                    elif response.status in [429, 503]:
                        print(f"⚠️ Очередь на серверах Google (Статус {response.status}). Ожидание...")
                        await asyncio.sleep(2 ** (attempt + 1))
                        continue
                        
                    # Если возникла другая ошибка (например, 400 или 404)
                    else:
                        error_text = await response.text()
                        print(f"❌ Ошибка Google API (Статус {response.status}):\n{error_text}")
                        raise Exception(f"API Error {response.status}")
                        
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            await asyncio.sleep(2 ** (attempt + 1))

    async with aiohttp.ClientSession() as session:
        for attempt in range(max_retries):
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    result_text = data['candidates'][0]['content']['parts'][0]['text']
                    return json.loads(result_text)
                
                # Если сервер перегружен - ждем и пробуем снова (2, 4, 8 сек)
                if response.status in [503, 429]:
                    error_text = await response.text()
                    print(f"\n⚠️ Скрытая ошибка от Google (Статус {response.status}):\n{error_text}\n")
                    wait_time = 2 ** (attempt + 1)
                    await asyncio.sleep(wait_time)
                    continue
                
                # При других ошибках (например, 400) падаем сразу
                response_text = await response.text()
                raise Exception(f"Ошибка API Gemini ({response.status}): {response_text}")
                
        raise Exception("Серверы Google AI сейчас перегружены. Попробуйте загрузить чек через пару минут.")


# ==========================================
# 2. РАБОТА С GOOGLE SHEETS И ДЕДУПЛИКАЦИЯ
# ==========================================
def get_sheets_service(refresh_token: str):
    """Восстанавливает доступ к Таблицам пользователя по его токену."""
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET")
    )
    return build('sheets', 'v4', credentials=creds)

async def process_and_deduplicate(user_spreadsheet_id: str, refresh_token: str, ai_data: dict, file_id: str) -> int:
    """
    Читает таблицу пользователя, генерирует композитные ключи, 
    отсеивает дубликаты и асинхронно записывает новые строки.
    Возвращает количество добавленных строк.
    """
    service = get_sheets_service(refresh_token)
    sheet = service.spreadsheets()
    
    is_receipt = ai_data.get("document_type") == "receipt"
    sheet_name = "ReceiptItems" if is_receipt else "BankData"
    range_name = f"{sheet_name}!A:I"
    
    # Асинхронно читаем существующие данные
    result = await asyncio.to_thread(
        sheet.values().get(spreadsheetId=user_spreadsheet_id, range=range_name).execute
    )
    existing_rows = result.get('values', [])
    
    existing_hashes = set()
    db_hash_counts = {}

    # Читаем базу и создаем слепки (хэши) существующих транзакций
    for row in existing_rows[1:]: # Пропускаем строку с заголовками
        if is_receipt and len(row) >= 9:
            date_str, merchant, item_name, amount, file_id_db = row[1], row[2], row[3], row[6], row[8]
        elif not is_receipt and len(row) >= 7:
            date_str, merchant, amount, file_id_db = row[1], row[2], row[3], row[6]
            item_name = "Покупка"
        else:
            continue
            
        base_hash = f"{date_str}_{amount}_{merchant}_{item_name}_{file_id_db}".lower()
        db_hash_counts[base_hash] = db_hash_counts.get(base_hash, 0) + 1
        final_hash = f"{base_hash}_{db_hash_counts[base_hash]}"
        existing_hashes.add(final_hash)

    # Фильтруем новые данные от ИИ
    rows_to_insert = []
    current_run_counts = {}
    
    items_to_process = ai_data.get("items", []) if is_receipt else ai_data.get("transactions", [])
    receipt_info = ai_data.get("receipt_info", {})
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for item in items_to_process:
        date = receipt_info.get("date") if is_receipt else item.get("date", "")
        merchant = receipt_info.get("merchant") if is_receipt else item.get("merchant", "")
        amount = item.get("total_price") if is_receipt else item.get("amount", 0)
        item_name = item.get("item_name") if is_receipt else "Покупка"
        
        # Хэш с учетом ID нового файла
        base_hash = f"{date}_{amount}_{merchant}_{item_name}_{file_id}".lower()
        current_run_counts[base_hash] = current_run_counts.get(base_hash, 0) + 1
        final_hash = f"{base_hash}_{current_run_counts[base_hash]}"
        
        # Если слепок уникален — готовим к записи
        if final_hash not in existing_hashes:
            if is_receipt:
                rows_to_insert.append([
                    current_time, date, merchant, item_name, 
                    item.get("quantity", 1), item.get("price_per_unit", amount), 
                    amount, item.get("category", "Не определено"), file_id
                ])
            else:
                rows_to_insert.append([
                    current_time, date, merchant, amount, 
                    item.get("currency", "RUB"), item.get("category", "Не определено"), file_id
                ])
            existing_hashes.add(final_hash)

    # Асинхронно записываем новые строки пачкой
    if rows_to_insert:
        body = {'values': rows_to_insert}
        await asyncio.to_thread(
            sheet.values().append(
                spreadsheetId=user_spreadsheet_id, 
                range=range_name,
                valueInputOption="USER_ENTERED", 
                body=body
            ).execute
        )
        
    return len(rows_to_insert)