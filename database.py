import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String

load_dotenv()

# Берем URL базы из .env (по умолчанию SQLite)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///app.db")

# Настраиваем асинхронный движок
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# Модель пользователя
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True)
    google_refresh_token = Column(String, nullable=True)
    spreadsheet_id = Column(String, nullable=True) # ID созданной гугл-таблицы

# Функция для автоматического создания таблиц при старте сервера
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)