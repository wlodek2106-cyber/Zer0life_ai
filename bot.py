"""Zer0Life Commerce AI Telegram bot with integrated P2P Market (Jupiter DEX)."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import io
import logging
import os
import secrets
import sqlite3
from typing import Final

import aiohttp
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile
from openai import AsyncOpenAI


LOGGER = logging.getLogger(__name__)
ROUTER = Router()

# ==================== КОНФИГУРАЦИЯ ====================
ZRL_MINT_ADDRESS: Final[str] = "ВАШ_ZRL_MINT_ADDRESS_ЗДЕСЬ"  # Замените на реальный Mint-адрес ZRL в Solana
SOL_MINT: Final[str] = "So11111111111111111111111111111111111111112"
USDC_MINT: Final[str] = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

USAGE_DB_PATH: Final[str] = os.getenv(
    "USAGE_DB_PATH",
    str(os.path.join(os.path.dirname(__file__), "usage.sqlite3")),
)
OPENAI_MODEL: Final[str] = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BOT_USERNAME: Final[str] = "ZEROLIFEAiCOMMERCE_bot"


# FSM для создания P2P ордера
class P2POrderState(StatesGroup):
    waiting_for_pair = State()
    waiting_for_type = State()
    waiting_for_amount = State()
    waiting_for_price = State()


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def init_db() -> None:
    """Инициализация базы данных для генераций, пользователей и P2P ордеров."""
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_usage (
                telegram_user_id INTEGER PRIMARY KEY,
                generations_used INTEGER NOT NULL DEFAULT 0,
                bonus_generations INTEGER NOT NULL DEFAULT 0,
                referrer_id INTEGER,
                referral_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_photos (
                photo_token TEXT PRIMARY KEY,
                telegram_user_id INTEGER NOT NULL,
                telegram_file_id INTEGER NOT NULL,
                caption TEXT
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


async def get_jupiter_prices() -> dict[str, float]:
    """Получает актуальные котировки ZRL к SOL и USDC через Jupiter Price API v3."""
    if "ВАШ_" in ZRL_MINT_ADDRESS:
        return {"ZRL_SOL": 0.0, "ZRL_USDC": 0.0}

    ids = f"{ZRL_MINT_ADDRESS},{SOL_MINT},{USDC_MINT}"
    url = f"https://api.jup.ag/price/v3?ids={ids}"
    prices = {"ZRL_SOL": 0.0, "ZRL_USDC": 0.0}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    res = data.get("data", {})
                    zrl_p = float(res.get(ZRL_MINT_ADDRESS, {}).get("price", 0))
                    sol_p = float(res.get(SOL_MINT, {}).get("price", 0))
                    usdc_p = float(res.get(USDC_MINT, {}).get("price", 1))

                    if zrl_p > 0 and sol_p > 0:
                        prices["ZRL_SOL"] = zrl_p / sol_p
                    if zrl_p > 0:
                        prices["ZRL_USDC"] = zrl_p / (usdc_p if usdc_p > 0 else 1.0)
        except Exception as e:
            LOGGER.error(f"Jupiter API error: {e}")

    return prices


# ==================== КЛАВИАТУРЫ ====================

def main_menu_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="💱 P2P Биржа (ZRL/SOL & USDC)",
                    callback_data="p2p:menu",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="💎 Купить Pro / Безлимит",
                    callback_data="sub:menu",
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
                    text="🔄 Обновить курсы",
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


# ==================== ОБРАБОТЧИКИ: ОСНОВНЫЕ ====================

@ROUTER.message(Command("start"))
async def start_handler(message: types.Message) -> None:
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_usage (telegram_user_id) VALUES (?)",
            (message.from_user.id,),
        )

    await message.answer(
        "👋 Добро пожаловать в **Zer0Life Commerce AI**.\n\n"
        "Отправьте фото товара для создания SEO-карточки или перейдите в P2P-биржу для обмена токенов.",
        reply_markup=main_menu_keyboard(),
    )


# ==================== ОБРАБОТЧИКИ: P2P БИРЖА ====================

@ROUTER.callback_query(F.data.in_({"p2p:menu", "p2p:refresh"}))
async def p2p_menu_handler(callback: types.CallbackQuery) -> None:
    await callback.answer("⏳ Загружаю курсы с Jupiter DEX...")
    if callback.message is None:
        return

    rates = await get_jupiter_prices()
    text = (
        "💱 **P2P Площадка Zer0Life (Solana Network)**\n\n"
        "Курсы привязаны к ликвидности **Jupiter DEX** в реальном времени:\n"
        f"🔹 **ZRL / SOL:** `{rates['ZRL_SOL']:.6f}` SOL\n"
        f"🔹 **ZRL / USDC:** `${rates['ZRL_USDC']:.4f}` USDC\n\n"
        "Выберите действие в меню ниже:"
    )

    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=p2p_menu_keyboard())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=p2p_menu_keyboard())


@ROUTER.callback_query(F.data == "p2p:back_home")
async def p2p_back_home(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "👋 Главное меню бота:",
            reply_markup=main_menu_keyboard(),
        )


@ROUTER.callback_query(F.data == "p2p:list_orders")
async def p2p_list_orders(callback: types.CallbackQuery) -> None:
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute(
            "SELECT order_id, seller_id, pair, order_type, amount, price FROM p2p_orders WHERE status = 'active' ORDER BY order_id DESC LIMIT 10"
        ).fetchall()

    if not orders:
        text = "📋 В данный момент активных ордеров на бирже нет."
    else:
        text = "📋 **Активные ордера на бирже:**\n\n"
        for o in orders:
            o_id, _, pair, o_type, amount, price = o
            emoji = "🟢 КУПИТЬ" if o_type == "BUY" else "🔴 ПРОДАТЬ"
            text += f"#{o_id} | {pair} | {emoji} | Кол-во: {amount} | Цена: {price}\n"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад в P2P меню", callback_data="p2p:menu")]
        ]
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ==================== СОЗДАНИЕ ОРДЕРА (FSM) ====================

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
    await callback.message.edit_text("💱 Выберите торговую пару:", reply_markup=keyboard)


@ROUTER.callback_query(P2POrderState.waiting_for_pair, F.data.startswith("pair:"))
async def p2p_choose_pair(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    pair = callback.data.split(":")[1]
    await state.update_data(pair=pair)

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="🟢 Купить", callback_data="type:BUY"),
                types.InlineKeyboardButton(text="🔴 Продать", callback_data="type:SELL"),
            ],
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="p2p:menu")],
        ]
    )
    await state.set_state(P2POrderState.waiting_for_type)
    await callback.message.edit_text(f"Вы выбрали пару **{pair}**.\nВыберите тип ордера:", parse_mode="Markdown", reply_markup=keyboard)


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
    await message.answer("Введите желаемую цену за 1 ZRL (или отправьте текущую рыночную цену цифрой):")


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
        "✅ **Ордер успешно создан и опубликован!**\n\n"
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
        text = "📦 У вас пока нет созданных ордеров."
    else:
        text = "📦 **Ваши ордера:**\n\n"
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

    LOGGER.info("Zer0Life Commerce AI P2P Bot is starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
