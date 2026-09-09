import asyncio
import base64
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
    with sqlite3.connect("usage.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                generations_left INTEGER DEFAULT 3,
                is_pro INTEGER DEFAULT 0,
                referred_by INTEGER,
                zrl_balance REAL DEFAULT 0.0
            )
        """)
        conn.commit()

init_db()

# Состояния FSM
class ListingStates(StatesGroup):
    waiting_for_photo = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    args = command.args  # Реферальный аргумент (если перешли по ссылке ?start=ID)

    with sqlite3.connect("usage.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, referred_by FROM users WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()

        if not user_row:
            # Новый пользователь
            referred_by = None
            if args and args.isdigit():
                ref_id = int(args)
                if ref_id != user_id:  # Защита от самореферала
                    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (ref_id,))
                    if cursor.fetchone():
                        referred_by = ref_id

            cursor.execute("INSERT INTO users (user_id, referred_by) VALUES (?, ?)", (user_id, referred_by))
            conn.commit()

            # Если пользователь пришел по реферальной ссылке — начисляем бонус пригласившему
            if referred_by:
                cursor.execute("UPDATE users SET generations_left = generations_left + 2 WHERE user_id = ?", (referred_by,))
                conn.commit()
                try:
                    await bot.send_message(
                        referred_by, 
                        "🎉 По вашей реферальной ссылке зарегистрировался новый пользователь! Вам начислено +2 бесплатные генерации."
                    )
                except Exception:
                    pass

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Создать листинг (SEO)", callback_data="start_listing")],
        [InlineKeyboardButton(text="💎 Купить PRO подписку", callback_data="buy_pro")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="referral")]
    ])
    
    await message.answer(
        "👋 Привет! Я Zer0Life Commerce AI Bot.\n\n"
        "Я помогу создать продающие SEO-описания и теги для Etsy, Shopify и Allegro на основе фото вашего товара.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# Меню реферальной системы
@dp.callback_query(F.data == "referral")
async def referral_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    bot_info = await bot.get_me()
    
    with sqlite3.connect("usage.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
        ref_count = cursor.fetchone()[0]

    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])

    text = (
        "👥 **Реферальная система**\n\n"
        "Приглашайте друзей и получайте **+2 бесплатные генерации** за каждого приглашенного!\n\n"
        f"🔗 Ваша реферальная ссылка:\n`{ref_link}`\n\n"
        f"📊 Приглашено друзей: **{ref_count}**"
    )
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

# Меню выбора метода оплаты PRO
@dp.callback_query(F.data == "buy_pro")
async def buy_pro_menu(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Revolut / Банковская карта", callback_data="pay_revolut")],
        [InlineKeyboardButton(text="🌐 PayPal", callback_data="pay_paypal")],
        [InlineKeyboardButton(text="🪙 Crypto (CryptoBot / USDT)", callback_data="pay_crypto")],
        [InlineKeyboardButton(text="💎 Оплатить токенами ZRL", callback_data="pay_zrl")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])
    await callback.message.edit_text(
        "💎 **Покупка PRO-статуса**\n\nВыберите удобный способ оплаты:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

# Хендлеры платежных систем
@dp.callback_query(F.data == "pay_revolut")
async def pay_revolut(callback: types.CallbackQuery):
    await callback.message.answer("🔗 Ссылка на оплату через **Revolut**: [Нажмите для оплаты](https://revolut.me/yourlink)", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "pay_paypal")
async def pay_paypal(callback: types.CallbackQuery):
    await callback.message.answer("🌐 Ссылка на оплату через **PayPal**: [Нажмите для оплаты](https://paypal.me/yourlink)", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "pay_crypto")
async def pay_crypto(callback: types.CallbackQuery):
    await callback.message.answer("🪙 Ссылка на оплату через **CryptoBot**: [Создать инвойс в крипте](https://t.me/CryptoBot)", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "pay_zrl")
async def pay_zrl(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    with sqlite3.connect("usage.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT zrl_balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        zrl_balance = row[0] if row else 0.0

    if zrl_balance >= 10.0:
        with sqlite3.connect("usage.sqlite3") as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET zrl_balance = zrl_balance - 10.0, is_pro = 1 WHERE user_id = ?", (user_id,))
            conn.commit()
        await callback.message.answer("✅ Успешно! Вы активировали PRO-статус за токены ZRL.")
    else:
        await callback.message.answer(f"❌ Недостаточно токенов ZRL. Ваш баланс: {zrl_balance} ZRL. Требуется: 10 ZRL.")
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Создать листинг (SEO)", callback_data="start_listing")],
        [InlineKeyboardButton(text="💎 Купить PRO подписку", callback_data="buy_pro")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="referral")]
    ])
    await callback.message.edit_text(
        "👋 Главное меню:\n\nЯ помогу создать продающие SEO-описания и теги для Etsy, Shopify и Allegro.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "start_listing")
async def start_listing(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📸 Пожалуйста, отправьте фотографию вашего товара:")
    await state.set_state(ListingStates.waiting_for_photo)
    await callback.answer()

@dp.message(ListingStates.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    with sqlite3.connect("usage.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT generations_left, is_pro FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        
    if not row:
        generations_left, is_pro = 3, 0
    else:
        generations_left, is_pro = row[0], row[1]
    
    if generations_left <= 0 and not is_pro:
        await message.answer("❌ У вас закончились бесплатные генерации. Пригласите друзей по реферальной ссылке или оформите PRO подписку.")
        await state.clear()
        return

    await message.answer("⏳ Анализирую товар и генерирую SEO-листинг (EN / PL)...")
    
    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        encoded_image = base64.b64encode(file_bytes.read()).decode("utf-8")
        
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
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}}
                    ]
                }
            ],
            max_tokens=1500
        )
        result_text = response.choices[0].message.content
        
        if not is_pro:
            with sqlite3.connect("usage.sqlite3") as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET generations_left = generations_left - 1 WHERE user_id = ?", (user_id,))
                conn.commit()
            
        await message.answer(result_text, parse_mode="Markdown")
        
    except Exception as e:
        logging.error(f"Error generating listing: {e}")
        await message.answer("⚠️ Произошла ошибка при обращении к AI. Попробуйте позже.")
    finally:
        await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
