import asyncio
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from openai import AsyncOpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Инициализация базы данных SQLite
def init_db():
    conn = sqlite3.connect("usage.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            generations_left INTEGER DEFAULT 3,
            is_pro INTEGER DEFAULT 0,
            referred_by INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Состояния FSM
class ListingStates(StatesGroup):
    waiting_for_photo = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect("usage.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
    conn.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Создать листинг (SEO)", callback_data="start_listing")],
        [InlineKeyboardButton(text="💎 Купить PRO / ZRL", callback_data="buy_pro")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="referral")]
    ])
    
    await message.answer(
        "👋 Привет! Я **Zer0Life Commerce AI Bot**.\n\n"
        "Я помогу создать продающие SEO-описания и теги для Etsy, Shopify и Allegro на основе фото вашего товара.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "start_listing")
async def start_listing(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📸 Пожалуйста, отправьте фотографию вашего товара:")
    await state.set_state(ListingStates.waiting_for_photo)
    await callback.answer()

@dp.message(ListingStates.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверка лимитов
    conn = sqlite3.connect("usage.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT generations_left, is_pro FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    generations_left, is_pro = row[0], row[1]
    
    if generations_left <= 0 and not is_pro:
        await message.answer("❌ У вас закончились бесплатные генерации. Пожалуйста, оформите PRO подписку.")
        conn.close()
        await state.clear()
        return

    await message.answer("⏳ Анализирую товар и генерирую SEO-листинг (EN / PL)...")
    
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    file_path = file_info.file_path
    file_bytes = await bot.download_file(file_path)
    
    # Запрос к OpenAI Vision
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert e-commerce copywriter. Analyze the product and generate an SEO-optimized listing title, description, and 13 tags in both English and Polish for Etsy/Allegro."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Generate a complete e-commerce listing based on this photo."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{BufferedInputFile(file_bytes.read(), filename='img.jpg').file.read().hex()}"}} # Упрощенная передача для контекста
                    ]
                }
            ]
        )
        result_text = response.choices[0].message.content
        
        # Списание лимита
        if not is_pro:
            cursor.execute("UPDATE users SET generations_left = generations_left - 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            
        await message.answer(result_text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error generating listing: {e}")
        await message.answer("⚠️ Произошла ошибка при обращении к AI. Попробуйте позже.")
    finally:
        conn.close()
        await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
