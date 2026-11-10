import os
import base64
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy.future import select

# Импортируем твои готовые модули
from database import async_session, User
from services import fetch_gemini_with_retry, process_and_deduplicate

# override=True принудительно стирает кэш терминала и берет свежие данные из .env
load_dotenv(override=True)

# Получаем токен и очищаем его от случайных пробелов по краям (.strip)
raw_token = os.getenv("BOT_TOKEN")
print(f"🔍 Диагностика: скрипт видит токен -> '{raw_token}'")

bot = Bot(token=raw_token.strip() if raw_token else "")
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    telegram_id = str(message.from_user.id)
    
    # Проверяем, есть ли пользователь в базе
    async with async_session() as session:
        result = await session.execute(select(User).filter_by(telegram_id=telegram_id))
        user = result.scalars().first()

    if user and user.google_refresh_token:
        await message.answer("✅ Твой аккаунт привязан! Просто кидай мне фото чека или скриншот банка.")
    else:
        # Генерируем ссылку на твой FastAPI сервер (main.py)
        auth_link = f"http://localhost:8000/auth/google?telegram_id={telegram_id}"
        await message.answer(
            "Привет! Я AI-трекер расходов.\n"
            "Чтобы я мог записывать траты в твою таблицу, нужно дать мне доступ к Google Диску.\n\n"
            "🔗 Скопируй эту ссылку и открой её в браузере:\n\n"
            f"{auth_link}"
        )

@dp.message(Command("set_sheet"))
async def cmd_set_sheet(message: Message):
    # Временная команда для MVP-тестирования
    try:
        sheet_id = message.text.split()[1]
        telegram_id = str(message.from_user.id)
        async with async_session() as session:
            result = await session.execute(select(User).filter_by(telegram_id=telegram_id))
            user = result.scalars().first()
            
            if user:
                user.spreadsheet_id = sheet_id
                await session.commit()
                await message.answer("✅ ID таблицы успешно сохранен!")
            else:
                await message.answer("Сначала нажми /start и авторизуйся.")
    except IndexError:
        await message.answer("Используй формат: /set_sheet ТВОЙ_ID_ТАБЛИЦЫ")

@dp.message(F.photo)
async def handle_photo(message: Message):
    telegram_id = str(message.from_user.id)
    
    # 1. Проверяем авторизацию и наличие таблицы
    async with async_session() as session:
        result = await session.execute(select(User).filter_by(telegram_id=telegram_id))
        user = result.scalars().first()
        
    if not user or not user.google_refresh_token:
        await message.answer("Сначала привяжи аккаунт Google через /start")
        return
        
    if not user.spreadsheet_id:
        await message.answer(
            "У тебя не привязана таблица. Создай пустую Google Таблицу, сделай в ней два листа: "
            "'BankData' и 'ReceiptItems', и пришли мне её ID через команду:\n\n/set_sheet ТВОЙ_ID"
        )
        return
        
    status_msg = await message.answer("⏳ Скачиваю фото...")
    
    try:
        # 2. Забираем фото с серверов Telegram в оперативную память
        photo = message.photo[-1] # Берем сжатие наилучшего качества
        file_id = photo.file_id
        file_info = await bot.get_file(file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        # Переводим в формат, который понимает Gemini
        base64_image = base64.b64encode(downloaded_file.read()).decode('utf-8')
        
        # 3. Отправляем в сервисный слой (ИИ)
        await status_msg.edit_text("🧠 Анализирую чек...")
        ai_data = await fetch_gemini_with_retry(base64_image, "image/jpeg")
        
        # 4. Отправляем в сервисный слой (Google Sheets)
        await status_msg.edit_text("📝 Проверяю дубли и записываю в таблицу...")
        added_count = await process_and_deduplicate(
            user_spreadsheet_id=user.spreadsheet_id,
            refresh_token=user.google_refresh_token,
            ai_data=ai_data,
            file_id=file_id
        )
        
        # 5. Выводим итог
        await status_msg.edit_text(f"✅ Готово! Добавлено новых записей: {added_count}")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка:\n{str(e)}")

async def main():
    print("🤖 Telegram-бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())