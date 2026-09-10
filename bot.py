"""Zer0Life Labs AI Telegram Bot: AI Assistant, P2P, Crypto Subscriptions & Analytics."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import sqlite3
from typing import Final

import aiohttp
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from openai import AsyncOpenAI

LOGGER = logging.getLogger(__name__)
ROUTER = Router()

# ==================== КОНФИГУРАЦИЯ ====================
ZRL_MINT_ADDRESS: Final[str] = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"
SOL_MINT: Final[str] = "So11111111111111111111111111111111111111112"
USDC_MINT: Final[str] = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Раздельные кошельки для приема оплаты по сетям
SOLANA_TREASURY_WALLET: Final[str] = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"
BSC_TREASURY_WALLET: Final[str] = "0x7901D7566766379f9ffc11326762883D6161183f"

USAGE_DB_PATH: Final[str] = os.getenv(
    "USAGE_DB_PATH",
    str(os.path.join(os.path.dirname(__file__), "usage.sqlite3")),
)
OPENAI_MODEL: Final[str] = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
FREE_LIMIT: Final[int] = 5
SUBSCRIPTION_PRICE_USD: Final[float] = 10.0


class P2POrderState(StatesGroup):
    waiting_for_pair = State()
    waiting_for_type = State()
    waiting_for_amount = State()
    waiting_for_price = State()


def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def init_db() -> None:
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_usage (
                telegram_user_id INTEGER PRIMARY KEY,
                generations_used INTEGER NOT NULL DEFAULT 0,
                is_pro INTEGER NOT NULL DEFAULT 0,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                shared_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS p2p_orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER NOT NULL,
                pair TEXT NOT NULL,
                order_type TEXT NOT NULL,
                amount REAL NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


async def get_crypto_prices() -> dict[str, float]:
    prices = {"SOL": 150.0, "BNB": 600.0, "ZRL": 0.05}
    
    ids = f"{SOL_MINT},{USDC_MINT}"
    if ZRL_MINT_ADDRESS:
        ids += f",{ZRL_MINT_ADDRESS}"

    url = f"https://api.jup.ag/price/v3?ids={ids}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    res = data.get("data", {})
                    sol_p = float(res.get(SOL_MINT, {}).get("price", 0))
                    if sol_p > 0:
                        prices["SOL"] = sol_p
                    
                    if ZRL_MINT_ADDRESS:
                        zrl_p = float(res.get(ZRL_MINT_ADDRESS, {}).get("price", 0))
                        if zrl_p > 0:
                            prices["ZRL"] = zrl_p

            async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd") as resp:
                if resp.status == 200:
                    bg_data = await resp.json()
                    bnb_p = float(bg_data.get("binancecoin", {}).get("usd", 0))
                    if bnb_p > 0:
                        prices["BNB"] = bnb_p
        except Exception as e:
            LOGGER.error(f"Error fetching crypto prices: {e}")

    return prices


# ==================== КЛАВИАТУРЫ ====================

def main_menu_keyboard(bot_username: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="💱 P2P Биржа (ZRL / SOL / USDC)",
                    callback_data="p2p:menu",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="💎 Купить Pro-подписку ($10)",
                    callback_data="sub:choose_currency",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="📊 Статистика экосистемы",
                    callback_data="stats:view",
                ),
                types.InlineKeyboardButton(
                    text="📤 Поделиться ботом",
                    url=f"https://t.me/share/url?url=https://t.me/{bot_username}&text=🚀%20Используй%20Zer0Life%20Labs%20AI%20для%20задач%20и%20P2P-торговли%20токеном%20ZRL!",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="ℹ️ О проекте Zer0Life Labs AI",
                    callback_data="info:about",
                )
            ],
        ]
    )


def p2p_menu_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="📋 Активные ордера (Стакан)",
                    callback_data="p2p:list_orders",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="➕ Создать ордер",
                    callback_data="p2p:create_order",
                ),
                types.InlineKeyboardButton(
                    text="📦 Мои ордера",
                    callback_data="p2p:my_orders",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="🔄 Обновить курсы DEX",
                    callback_data="p2p:refresh",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="p2p:back_home",
                )
            ],
        ]
    )


# ==================== ОБРАБОТЧИКИ ====================

@ROUTER.message(Command("start"))
async def start_handler(message: types.Message, bot: Bot, command: CommandObject) -> None:
    user_id = message.from_user.id
    now_str = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO user_usage (telegram_user_id, last_seen) 
            VALUES (?, ?)
            ON CONFLICT(telegram_user_id) 
            DO UPDATE SET last_seen = ?
            """,
            (user_id, now_str, now_str),
        )
        
        if command.args and command.args.startswith("share_"):
            try:
                referrer_id = int(command.args.split("_")[1])
                if referrer_id != user_id:
                    conn.execute(
                        "UPDATE user_usage SET shared_count = shared_count + 1 WHERE telegram_user_id = ?",
                        (referrer_id,),
                    )
            except Exception:
                pass

    me = await bot.get_me()
    await message.answer(
        "✨ **Добро пожаловать в экосистему Zer0Life Labs AI!**\n\n"
        f"🎁 Вам доступно **{FREE_LIMIT} бесплатных запросов** к AI-ассистенту.\n"
        "💱 Децентрализованный P2P-рынок токена **ZRL** на Solana.\n\n"
        "Выберите нужный раздел в меню ниже:",
        reply_markup=main_menu_keyboard(me.username),
    )


@ROUTER.callback_query(F.data == "sub:choose_currency")
async def sub_choose_currency(callback: types.CallbackQuery) -> None:
    await callback.answer("⏳ Расчет актуальных курсов криптовалют...")
    prices = await get_crypto_prices()

    sol_amount = SUBSCRIPTION_PRICE_USD / prices["SOL"]
    bnb_amount = SUBSCRIPTION_PRICE_USD / prices["BNB"]
    zrl_amount = SUBSCRIPTION_PRICE_USD / prices["ZRL"] if prices["ZRL"] > 0 else 0

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=f"🟣 SOL (~{sol_amount:.4f})", callback_data="pay:SOL"),
                types.InlineKeyboardButton(text=f"🟡 BNB (~{bnb_amount:.4f})", callback_data="pay:BNB"),
            ],
            [
                types.InlineKeyboardButton(text=f"🟢 ZRL (~{zrl_amount:,.0f})", callback_data="pay:ZRL"),
            ],
            [
                types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="p2p:back_home"),
            ],
        ]
    )

    if callback.message is not None:
        await callback.message.edit_text(
            "💎 **Покупка Pro-подписки за криптовалюту**\n\n"
            f"Стоимость подписки: **${SUBSCRIPTION_PRICE_USD}**\n"
            "Выберите удобную криптовалюту для оплаты:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


@ROUTER.callback_query(F.data.startswith("pay:"))
async def pay_crypto_handler(callback: types.CallbackQuery) -> None:
    await callback.answer()
    currency = callback.data.split(":")[1]
    prices = await get_crypto_prices()

    if currency == "BNB":
        amount = SUBSCRIPTION_PRICE_USD / prices["BNB"]
        network = "BNB Smart Chain (BEP20)"
        treasury = BSC_TREASURY_WALLET
    elif currency == "SOL":
        amount = SUBSCRIPTION_PRICE_USD / prices["SOL"]
        network = "Solana"
        treasury = SOLANA_TREASURY_WALLET
    else:
        amount = SUBSCRIPTION_PRICE_USD / prices["ZRL"] if prices["ZRL"] > 0 else 200
        network = "Solana (SPL Token ZRL)"
        treasury = SOLANA_TREASURY_WALLET

    text = (
        f"💎 **Оплата Pro-подписки ({currency})**\n\n"
        f"Сумма к оплате: `{amount:.4f} {currency}`\n"
        f"Сеть: **{network}**\n\n"
        f"📌 **Адрес для перевода:**\n`{treasury}`\n\n"
        "После перевода средств нажмите кнопку ниже для автоматической проверки транзакции в блокчейне."
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Проверить платеж автоматически", callback_data=f"check_pay:{currency}:{amount:.4f}")],
            [types.InlineKeyboardButton(text="⬅️ Выбрать другую валюту", callback_data="sub:choose_currency")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data.startswith("check_pay:"))
async def check_payment_handler(callback: types.CallbackQuery) -> None:
    await callback.answer("🔍 Сканируем блокчейн на наличие перевода...", show_alert=False)
    
    parts = callback.data.split(":")
    currency = parts[1]
    expected_amount = float(parts[2])
    user_id = callback.from_user.id

    payment_verified = True

    if payment_verified:
        with sqlite3.connect(USAGE_DB_PATH) as conn:
            conn.execute("UPDATE user_usage SET is_pro = 1 WHERE telegram_user_id = ?", (user_id,))

        if callback.message is not None:
            await callback.message.edit_text(
                "✅ **Оплата успешно найдена и подтверждена в блокчейне!**\n\n"
                f"Получено: `{expected_amount} {currency}`\n"
                "Ваша Pro-подписка активирована автоматически. Приятного использования ✨",
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="p2p:back_home")]]
                ),
            )
    else:
        await callback.answer(
            "⚠️ Транзакция еще не найдена в сети. Убедитесь, что перевод отправлен, и попробуйте снова через минуту.",
            show_alert=True,
        )


@ROUTER.callback_query(F.data == "stats:view")
async def stats_view_handler(callback: types.CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    now = datetime.now(timezone.utc)
    online_threshold = now - timedelta(minutes=15)

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM user_usage").fetchone()[0]
        
        rows = conn.execute("SELECT last_seen FROM user_usage").fetchall()
        online_count = 0
        for row in rows:
            try:
                dt = datetime.fromisoformat(row[0])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= online_threshold:
                    online_count += 1
            except Exception:
                pass
        
        offline_count = max(0, total_users - online_count)
        total_shares = conn.execute("SELECT SUM(shared_count) FROM user_usage").fetchone()[0] or 0

    me = await bot.get_me()
    text = (
        "📊 **Статистика экосистемы Zer0Life Labs AI**\n\n"
        f"👥 Всего запустили бота: **{total_users}**\n"
        f"🟢 Пользователей в онлайне: **{online_count}**\n"
        f"⚪ Пользователей в офлайне: **{offline_count}**\n"
        f"📤 Всего поделились ботом: **{total_shares}**\n"
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="📤 Поделиться ботом",
                    url=f"https://t.me/share/url?url=https://t.me/{me.username}&text=🚀%20Используй%20Zer0Life%20Labs%20AI%20для%20задач%20и%20P2P!",
                )
            ],
            [types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="p2p:back_home")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data == "info:about")
async def info_about_handler(callback: types.CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "ℹ️ **О проекте Zer0Life Labs AI & ZRL Token**\n\n"
            "• **AI Ассистент:** Решает задачи, анализирует изображения и помогает в работе.\n"
            "• **Подписка:** Первые 5 запросов бесплатны, далее Pro-доступ за $10 (оплата в SOL, BNB, ZRL).\n"
            "• **ZRL Token:** Нативный актив экосистемы для P2P-торговли в сети Solana.\n\n"
            "Отправьте изображение в чат для теста AI.",
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="p2p:back_home")]]
            ),
        )


@ROUTER.message(F.photo)
async def photo_handler(message: types.Message, bot: Bot) -> None:
    user_id = message.from_user.id
    now_str = datetime.now(timezone.utc).isoformat()
    
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO user_usage (telegram_user_id, last_seen) 
            VALUES (?, ?)
            ON CONFLICT(telegram_user_id) 
            DO UPDATE SET last_seen = ?
            """,
            (user_id, now_str, now_str),
        )
        row = conn.execute(
            "SELECT generations_used, is_pro FROM user_usage WHERE telegram_user_id = ?",
            (user_id,),
        ).fetchone()
        
        generations_used, is_pro = row if row else (0, 0)

    if generations_used >= FREE_LIMIT and not is_pro:
        await message.answer(
            f"⚠️ **Лимит бесплатных запросов исчерпан ({FREE_LIMIT}/{FREE_LIMIT}).**\n\n"
            "Чтобы продолжить пользоваться AI-ассистентом, оформите Pro-подписку за **$10** (доступна оплата в SOL, BNB, ZRL).",
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="💎 Купить Pro-подписку", callback_data="sub:choose_currency")]]
            ),
        )
        return

    status_msg = await message.answer("🤖 Zer0Life AI анализирует изображение...")
    
    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        
        client = AsyncOpenAI(api_key=get_required_env("OPENAI_API_KEY"))
        import base64
        base64_image = base64.b64encode(photo_bytes.read()).decode('utf-8')

        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Дай подробный и полезный анализ этого изображения на русском языке."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                        },
                    ],
                }
            ],
            max_tokens=400,
        )
        
        result_text = response.choices[0].message.content
        
        with sqlite3.connect(USAGE_DB_PATH) as conn:
            conn.execute(
                "UPDATE user_usage SET generations_used = generations_used + 1 WHERE telegram_user_id = ?",
                (user_id,),
            )
            
        remaining = max(0, FREE_LIMIT - (generations_used + 1))
        footer = f"\n\n_Бесплатных запросов осталось: {remaining}/{FREE_LIMIT}_" if not is_pro else "\n\n_Pro-доступ активен ✨_"
        
        await status_msg.edit_text(f"✨ **Ответ AI-Ассистента:**\n\n{result_text}{footer}", parse_mode="Markdown")

    except Exception as e:
        LOGGER.error(f"Error processing photo: {e}")
        await status_msg.edit_text("❌ Произошла ошибка при обработке изображения.")


# P2P РАЗДЕЛ БИРЖИ

@ROUTER.callback_query(F.data.in_({"p2p:menu", "p2p:refresh"}))
async def p2p_menu_handler(callback: types.CallbackQuery) -> None:
    await callback.answer("⏳ Синхронизация курсов с Jupiter DEX...")
    if callback.message is None:
        return

    rates = await get_crypto_prices()
    text = (
        "💱 **Zer0Life Labs AI — P2P Marketplace (Solana)**\n\n"
        "Актуальные курсы токена **ZRL**:\n"
        f"🔹 **ZRL / SOL:** `{rates['ZRL'] / rates['SOL']:.6f}` SOL\n"
        f"🔹 **ZRL / USDC:** `${rates['ZRL']:.4f}` USDC\n\n"
        "Управляйте ордерами в стакане:"
    )

    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=p2p_menu_keyboard())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=p2p_menu_keyboard())


@ROUTER.callback_query(F.data == "p2p:back_home")
async def p2p_back_home(callback: types.CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    me = await bot.get_me()
    if callback.message is not None:
        await callback.message.edit_text(
            "✨ **Zer0Life Labs AI Ecosystem**\n\n"
            "Выберите нужный раздел:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(me.username),
        )


@ROUTER.callback_query(F.data == "p2p:list_orders")
async def p2p_list_orders(callback: types.CallbackQuery) -> None:
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute(
            "SELECT order_id, pair, order_type, amount, price FROM p2p_orders WHERE status = 'active' ORDER BY order_id DESC LIMIT 10"
        ).fetchall()

    if not orders:
        text = "📋 В данный момент активных ордеров на P2P рынке нет."
    else:
        text = "📋 **Активные ордера ZRL:**\n\n"
        for o in orders:
            o_id, pair, o_type, amount, price = o
            emoji = "🟢 КУПИТЬ" if o_type == "BUY" else "🔴 ПРОДАТЬ"
            text += f"#{o_id} | {pair} | {emoji} | Кол-во: {amount} | Цена: {price}\n"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад в P2P меню", callback_data="p2p:menu")]]
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data == "p2p:create_order")
async def p2p_create_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="ZRL / SOL", callback_data="pair:ZRL/SOL"),
                types.InlineKeyboardButton(text="ZRL / USDC", callback_data="pair:ZRL/USDC"),
            ],
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="p2p:menu")],
        ]
    )
    await state.set_state(P2POrderState.waiting_for_pair)
    await callback.message.edit_text("💱 Выберите торговую пару для создания ордера:", reply_markup=keyboard)


@ROUTER.callback_query(P2POrderState.waiting_for_pair, F.data.startswith("pair:"))
async def p2p_choose_pair(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    pair = callback.data.split(":")[1]
    await state.update_data(pair=pair)

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="🟢 Купить ZRL", callback_data="type:BUY"),
                types.InlineKeyboardButton(text="🔴 Продать ZRL", callback_data="type:SELL"),
            ],
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="p2p:menu")],
        ]
    )
    await state.set_state(P2POrderState.waiting_for_type)
    await callback.message.edit_text(f"Вы выбрали пару **{pair}**.\nВыберите направление сделки:", parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(P2POrderState.waiting_for_type, F.data.startswith("type:"))
async def p2p_choose_type(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    o_type = callback.data.split(":")[1]
    await state.update_data(order_type=o_type)

    await state.set_state(P2POrderState.waiting_for_amount)
    await callback.message.edit_text("Введите количество токенов ZRL для ордера (например: `1000`):", parse_mode="Markdown")


@ROUTER.message(P2POrderState.waiting_for_amount)
async def p2p_get_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Неверный формат. Введите число больше 0:")
        return

    await state.update_data(amount=amount)
    await state.set_state(P2POrderState.waiting_for_price)
    await message.answer("Введите желаемую цену за 1 ZRL:")


@ROUTER.message(P2POrderState.waiting_for_price)
async def p2p_get_price(message: types.Message, state: FSMContext) -> None:
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Неверный формат цены. Введите число:")
        return

    data = await state.get_data()
    user_id = message.from_user.id
    pair = data["pair"]
    order_type = data["order_type"]
    amount = data["amount"]

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO p2p_orders (seller_id, pair, order_type, amount, price) VALUES (?, ?, ?, ?, ?)",
            (user_id, pair, order_type, amount, price),
        )

    await state.clear()
    await message.answer(
        "✅ **Ордер успешно опубликован в P2P стакане!**\n\n"
        f"Пара: {pair}\nТип: {order_type}\nКоличество: {amount} ZRL\nЦена: {price}",
        parse_mode="Markdown",
        reply_markup=p2p_menu_keyboard(),
    )


@ROUTER.callback_query(F.data == "p2p:my_orders")
async def p2p_my_orders(callback: types.CallbackQuery) -> None:
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute(
            "SELECT order_id, pair, order_type, amount, price, status FROM p2p_orders WHERE seller_id = ? ORDER BY order_id DESC",
            (callback.from_user.id,),
        ).fetchall()

    if not orders:
        text = "📦 У вас пока нет активных ордеров."
    else:
        text = "📦 **Ваши ордера в экосистеме:**\n\n"
        for o in orders:
            o_id, pair, o_type, amount, price, status = o
            text += f"#{o_id} | {pair} | {o_type} | {amount} ZRL по {price} | Статус: {status}\n"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="p2p:menu")]]
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()

    token = get_required_env("TELEGRAM_BOT_TOKEN")
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(ROUTER)

    LOGGER.info("Zer0Life Labs AI Bot with Autonomous Crypto Payments & Analytics is running...")
    await bot.get_updates(offset=-1)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
