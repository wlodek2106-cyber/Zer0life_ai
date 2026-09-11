"""Zer0Life Labs AI Telegram Bot: AI Assistant, P2P Escrow, Crypto Subscriptions, Auto-Trading, Merch & ZRL Airdrop Mini-Game."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import logging
import os
import random
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

# ==================== CONFIGURATION ====================
ZRL_MINT_ADDRESS: Final[str] = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"
SOL_MINT: Final[str] = "So11111111111111111111111111111111111111112"
USDC_MINT: Final[str] = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

SOLANA_RPC_URL: Final[str] = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
SOLANA_TREASURY_WALLET: Final[str] = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"
BSC_TREASURY_WALLET: Final[str] = "0x7901D7566766379f9ffc11326762883D6161183f"

TOTAL_AIRDROP_POOL: Final[float] = 100_000_000.0
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))

ETSY_URL: Final[str] = os.getenv("ETSY_URL", "https://www.etsy.com")
ALLEGRO_URL: Final[str] = os.getenv("ALLEGRO_URL", "https://allegro.pl")
AMAZON_URL: Final[str] = os.getenv("AMAZON_URL", "https://www.amazon.com")

USAGE_DB_PATH: Final[str] = os.getenv(
    "USAGE_DB_PATH",
    str(os.path.join(os.path.dirname(__file__), "usage.sqlite3")),
)
OPENAI_MODEL: Final[str] = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
FREE_LIMIT: Final[int] = 5
SUBSCRIPTION_PRICE_USD: Final[float] = 10.0


# ==================== LOCALIZATION DICTIONARY ====================
TRANSLATIONS = {
    "en": {
        "welcome": (
            "✨ **Welcome to Zer0Life Labs AI Ecosystem!**\n\n"
            "🎁 You have **{limit} free AI requests** available.\n"
            "🤖 Launch the **ZRL Airdrop** mini-game, Auto-Trading, or P2P market on Solana.\n\n"
            "Select a section from the menu below:"
        ),
        "btn_airdrop": "🎁 Join ZRL Airdrop",
        "btn_autotrade": "🤖 Auto-Trading Bot",
        "btn_p2p": "💱 P2P Marketplace (ZRL / SOL / USDC)",
        "btn_merch": "🛍 Official Merch Store",
        "btn_sub": "💎 Buy Pro Subscription ($10)",
        "btn_stats": "📊 Ecosystem Stats",
        "btn_share": "📤 Share Bot",
        "btn_about": "ℹ️ About Zer0Life Labs AI",
        "btn_lang": "🌐 Language: English",
        "back_home": "🏠 Main Menu",
        "lang_updated": "✅ Language successfully changed to English!",
        "merch_title": (
            "🛍 **Zer0Life Labs Official Merchandise**\n\n"
            "Choose your preferred marketplace to check out our products:"
        ),
        "btn_etsy": "🛒 Etsy Shop",
        "btn_allegro": "📦 Allegro Store",
        "btn_amazon": "📦 Amazon Store",
    },
    "ru": {
        "welcome": (
            "✨ **Добро пожаловать в экосистему Zer0Life Labs AI!**\n\n"
            "🎁 Вам доступно **{limit} бесплатных запросов** к AI-ассистенту.\n"
            "🤖 Запустите мини-игру **Airdrop ZRL**, Авто-трейдинг или P2P-рынок в сети Solana.\n\n"
            "Выберите нужный раздел в меню ниже:"
        ),
        "btn_airdrop": "🎁 Участвовать в Airdrop ZRL",
        "btn_autotrade": "🤖 Авто-трейдинг бот",
        "btn_p2p": "💱 P2P Биржа (ZRL / SOL / USDC)",
        "btn_merch": "🛍 Наш Мерч (Магазины)",
        "btn_sub": "💎 Купить Pro-подписку ($10)",
        "btn_stats": "📊 Статистика экосистемы",
        "btn_share": "📤 Поделиться ботом",
        "btn_about": "ℹ️ О проекте Zer0Life Labs AI",
        "btn_lang": "🌐 Язык: Русский",
        "back_home": "🏠 Главное меню",
        "lang_updated": "✅ Язык успешно изменен на русский!",
        "merch_title": (
            "🛍 **Официальный Мерч Zer0Life Labs**\n\n"
            "Выберите удобную платформу для просмотра и покупки наших товаров:"
        ),
        "btn_etsy": "🛒 Магазин Etsy",
        "btn_allegro": "📦 Магазин Allegro",
        "btn_amazon": "📦 Магазин Amazon",
    }
}


def get_text(user_id: int, key: str, **kwargs) -> str:
    lang = "en"
    try:
        with sqlite3.connect(USAGE_DB_PATH) as conn:
            row = conn.execute("SELECT language FROM user_usage WHERE telegram_user_id = ?", (user_id,)).fetchone()
            if row and row[0]:
                lang = row[0]
    except Exception:
        pass
    text_template = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"].get(key, key))
    return text_template.format(**kwargs)


class P2POrderState(StatesGroup):
    waiting_for_pair = State()
    waiting_for_type = State()
    waiting_for_amount = State()
    waiting_for_price = State()
    waiting_for_payment_address = State()


class P2PDealState(StatesGroup):
    waiting_for_proof = State()


class AutoTradeSettingsState(StatesGroup):
    waiting_for_pair = State()
    waiting_for_amount = State()
    waiting_for_target = State()


class AirdropClaimState(StatesGroup):
    waiting_for_solana_address = State()


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
                language TEXT NOT NULL DEFAULT 'en',
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
                payment_address TEXT NOT NULL DEFAULT '',
                buyer_id INTEGER,
                proof_photo_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_trade (
                user_id INTEGER PRIMARY KEY,
                pair TEXT,
                amount REAL,
                target_percent REAL,
                status TEXT DEFAULT 'inactive'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS airdrop_claims (
                user_id INTEGER PRIMARY KEY,
                reward_amount REAL NOT NULL,
                solana_address TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


async def check_solana_tx(signature: str) -> bool:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignatureStatuses",
        "params": [[signature]]
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(SOLANA_RPC_URL, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    statuses = data.get("result", {}).get("value", [])
                    if statuses and statuses[0]:
                        confirmation = statuses[0].get("confirmationStatus")
                        if confirmation in ("confirmed", "finalized"):
                            return True
        except Exception as e:
            LOGGER.error(f"Error checking Solana TX: {e}")
    return False


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


# ==================== KEYBOARDS ====================

def main_menu_keyboard(bot_username: str, user_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_airdrop"), callback_data="airdrop:menu")],
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_autotrade"), callback_data="autotrade:menu")],
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_p2p"), callback_data="p2p:menu")],
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_merch"), callback_data="merch:menu")],
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_sub"), callback_data="sub:choose_currency")],
            [
                types.InlineKeyboardButton(text=get_text(user_id, "btn_stats"), callback_data="stats:view"),
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_share"),
                    url=f"https://t.me/share/url?url=https://t.me/{bot_username}&text=🚀%20Use%20Zer0Life%20Labs%20AI!",
                ),
            ],
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_about"), callback_data="info:about")],
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_lang"), callback_data="lang:choose")],
        ]
    )


def p2p_menu_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="📋 Активные ордера (Стакан)", callback_data="p2p:list_orders")],
            [
                types.InlineKeyboardButton(text="➕ Создать ордер", callback_data="p2p:create_order"),
                types.InlineKeyboardButton(text="📦 Мои ордера", callback_data="p2p:my_orders"),
            ],
            [types.InlineKeyboardButton(text="🔄 Обновить курсы DEX", callback_data="p2p:refresh")],
            [types.InlineKeyboardButton(text="⬅️ Главное меню", callback_data="p2p:back_home")],
        ]
    )


# ==================== HANDLERS ====================

@ROUTER.message(Command("start"))
async def start_handler(message: types.Message, bot: Bot, command: CommandObject) -> None:
    user_id = message.from_user.id
    now_str = datetime.now(timezone.utc).isoformat()
    tg_lang = message.from_user.language_code
    default_lang = "ru" if tg_lang and tg_lang.startswith("ru") else "en"

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        existing = conn.execute("SELECT language FROM user_usage WHERE telegram_user_id = ?", (user_id,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO user_usage (telegram_user_id, language, last_seen) VALUES (?, ?, ?)",
                (user_id, default_lang, now_str),
            )
        else:
            conn.execute("UPDATE user_usage SET last_seen = ? WHERE telegram_user_id = ?", (now_str, user_id))

    me = await bot.get_me()
    await message.answer(
        get_text(user_id, "welcome", limit=FREE_LIMIT),
        reply_markup=main_menu_keyboard(me.username, user_id),
        parse_mode="Markdown",
    )


# --- P2P ESCROW SECTION ---

@ROUTER.callback_query(F.data.in_({"p2p:menu", "p2p:refresh"}))
async def p2p_menu_handler(callback: types.CallbackQuery) -> None:
    await callback.answer("⏳ Синхронизация с Jupiter DEX...")
    if callback.message is None:
        return

    rates = await get_crypto_prices()
    text = (
        "💱 **Zer0Life Labs AI — P2P Биржа (Solana Escrow)**\n\n"
        "Текущие курсы токена **ZRL**:\n"
        f"🔹 **ZRL / SOL:** `{rates['ZRL'] / rates['SOL']:.6f}` SOL\n"
        f"🔹 **ZRL / USDC:** `${rates['ZRL']:.4f}` USDC\n\n"
        "Управляйте заявками в стакане:"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=p2p_menu_keyboard())


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
                types.InlineKeyboardButton(text="🟢 Купить ZRL", callback_data="type:BUY"),
                types.InlineKeyboardButton(text="🔴 Продать ZRL", callback_data="type:SELL"),
            ],
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="p2p:menu")],
        ]
    )
    await state.set_state(P2POrderState.waiting_for_type)
    await callback.message.edit_text(f"Пара **{pair}**.\nВыберите направление ордера:", parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(P2POrderState.waiting_for_type, F.data.startswith("type:"))
async def p2p_choose_type(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    o_type = callback.data.split(":")[1]
    await state.update_data(order_type=o_type)
    await state.set_state(P2POrderState.waiting_for_amount)
    await callback.message.edit_text("Введите количество токенов ZRL (например, `1000`):", parse_mode="Markdown")


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
    await message.answer("Введите цену за 1 ZRL:")


@ROUTER.message(P2POrderState.waiting_for_price)
async def p2p_get_price(message: types.Message, state: FSMContext) -> None:
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Неверный формат цены. Введите число больше 0:")
        return

    await state.update_data(price=price)
    data = await state.get_data()
    currency_symbol = data["pair"].split("/")[1]

    await state.set_state(P2POrderState.waiting_for_payment_address)
    await message.answer(
        f"👛 **Укажите ваш Solana-кошелек** для этой сделки.\n\n"
        f"Сюда вам будут отправлены **{currency_symbol if data['order_type'] == 'SELL' else 'ZRL'}** после выполнения заказа:",
        parse_mode="Markdown",
    )


@ROUTER.message(P2POrderState.waiting_for_payment_address)
async def p2p_get_payment_address(message: types.Message, state: FSMContext) -> None:
    address = message.text.strip()
    if len(address) < 32 or len(address) > 44:
        await message.answer("⚠️ Неверный адрес Solana. Введите корректный кошелек:")
        return

    data = await state.get_data()
    user_id = message.from_user.id
    pair = data["pair"]
    order_type = data["order_type"]
    amount = data["amount"]
    price = data["price"]

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO p2p_orders (seller_id, pair, order_type, amount, price, payment_address)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, pair, order_type, amount, price, address),
        )

    await state.clear()
    total_sum = amount * price
    currency_symbol = pair.split("/")[1]

    await message.answer(
        "✅ **Ордер успешно создан и добавлен в стакан!**\n\n"
        f"• Пара: `{pair}`\n"
        f"• Тип: `{order_type}`\n"
        f"• Объем: `{amount:,.0f} ZRL`\n"
        f"• Цена: `{price} {currency_symbol}`\n"
        f"• Итоговая сумма: `{total_sum:,.4f} {currency_symbol}`\n"
        f"• Реквизиты получения: `{address}`",
        parse_mode="Markdown",
        reply_markup=p2p_menu_keyboard(),
    )


@ROUTER.callback_query(F.data == "p2p:list_orders")
async def p2p_list_orders(callback: types.CallbackQuery) -> None:
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute(
            "SELECT order_id, seller_id, pair, order_type, amount, price FROM p2p_orders WHERE status = 'active' ORDER BY order_id DESC LIMIT 10"
        ).fetchall()

    if not orders:
        text = "📋 В данный момент нет активных ордеров."
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад в P2P", callback_data="p2p:menu")]]
        )
    else:
        text = "📋 **Активные ордера ZRL (выберите для сделки):**\n\n"
        keyboard_buttons = []
        for o in orders:
            o_id, seller_id, pair, o_type, amount, price = o
            emoji = "🟢 КУПИТЬ" if o_type == "BUY" else "🔴 ПРОДАТЬ"
            total_sum = amount * price
            currency_symbol = pair.split("/")[1]
            text += f"#{o_id} | **{pair}** | {emoji}\n🔹 Объем: `{amount:,.0f} ZRL`\n🔹 Цена: `{price} {currency_symbol}` (Всего: `{total_sum:,.4f} {currency_symbol}`)\n\n"
            keyboard_buttons.append([
                types.InlineKeyboardButton(text=f"🤝 Принять ордер #{o_id} ({o_type})", callback_data=f"p2p:take:{o_id}")
            ])
        keyboard_buttons.append([types.InlineKeyboardButton(text="⬅️ Назад в P2P", callback_data="p2p:menu")])
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data.startswith("p2p:take:"))
async def p2p_take_order(callback: types.CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.split(":")[2])
    buyer_id = callback.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        order = conn.execute(
            "SELECT seller_id, pair, order_type, amount, price, payment_address, status FROM p2p_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()

        if not order or order[6] != "active":
            await callback.answer("❌ Ордер не найден или уже исполняется.", show_alert=True)
            return

        seller_id, pair, o_type, amount, price, address, status = order
        if seller_id == buyer_id:
            await callback.answer("❌ Нельзя исполнить собственный ордер.", show_alert=True)
            return

        conn.execute(
            "UPDATE p2p_orders SET buyer_id = ?, status = 'awaiting_payment' WHERE order_id = ?",
            (buyer_id, order_id),
        )

    await state.update_data(active_order_id=order_id)
    await state.set_state(P2PDealState.waiting_for_proof)

    total_sum = amount * price
    currency_symbol = pair.split("/")[1]

    text = (
        f"⏳ **Вы приняли сделку по ордеру #{order_id}!**\n\n"
        f"• Пара: `{pair}`\n"
        f"• Объем: `{amount:,.0f} ZRL`\n"
        f"• Сумма к оплате: `{total_sum:,.4f} {currency_symbol}`\n\n"
        f"📌 **Отправьте средства на Solana-адрес продавца:**\n"
        f"`{address}`\n\n"
        "После перевода **отправьте скриншот чека/транзакции или ее хэш (tx signature)** прямо в этот чат!"
    )

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="❌ Отменить сделку", callback_data="p2p:menu")]]
        ),
    )


@ROUTER.message(P2PDealState.waiting_for_proof)
async def p2p_receive_proof(message: types.Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    order_id = data.get("active_order_id")
    buyer_id = message.from_user.id

    proof_photo_id = None
    tx_hash = None

    if message.photo:
        proof_photo_id = message.photo[-1].file_id
    elif message.text:
        tx_hash = message.text.strip()
        if len(tx_hash) >= 64:
            is_valid = await check_solana_tx(tx_hash)
            if is_valid:
                await message.answer("✅ Транзакция подтверждена в блокчейне Solana!")

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            "UPDATE p2p_orders SET status = 'paid', proof_photo_id = ? WHERE order_id = ?",
            (proof_photo_id or tx_hash, order_id),
        )
        row = conn.execute("SELECT seller_id, pair, amount, price FROM p2p_orders WHERE order_id = ?", (order_id,)).fetchone()

    await state.clear()
    await message.answer("👍 **Подтверждение оплаты отправлено продавцу!** Ожидайте подтверждения получения.")

    if row:
        seller_id, pair, amount, price = row
        total_sum = amount * price
        currency_symbol = pair.split("/")[1]

        seller_text = (
            f"🔔 **Покупатель отметил оплату по ордеру #{order_id}!**\n\n"
            f"• Сумма: `{total_sum:,.4f} {currency_symbol}`\n"
            f"• Доказательство: {tx_hash if tx_hash else 'Скриншот прикреплен ниже'}\n\n"
            "Проверьте поступление средств на ваш кошелек и нажмите кнопку подтверждения:"
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="✅ Подтвердить получение средств", callback_data=f"p2p:confirm:{order_id}")]]
        )

        try:
            if proof_photo_id:
                await bot.send_photo(seller_id, photo=proof_photo_id, caption=seller_text, parse_mode="Markdown", reply_markup=keyboard)
            else:
                await bot.send_message(seller_id, seller_text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception as e:
            LOGGER.error(f"Failed to notify seller: {e}")


@ROUTER.callback_query(F.data.startswith("p2p:confirm:"))
async def p2p_confirm_deal(callback: types.CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.split(":")[2])
    seller_id = callback.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute("SELECT buyer_id, pair, amount, price, status FROM p2p_orders WHERE order_id = ?", (order_id,)).fetchone()

        if not row:
            await callback.answer("❌ Ордер не найден.", show_alert=True)
            return

        buyer_id, pair, amount, price, status = row
        conn.execute("UPDATE p2p_orders SET status = 'completed' WHERE order_id = ?", (order_id,))

    await callback.answer("🎉 Сделка успешно завершена!", show_alert=True)
    await callback.message.edit_text(f"✅ **Ордер #{order_id} закрыт.** Средства успешно переведены!")

    try:
        await bot.send_message(
            buyer_id,
            f"🎉 **Продавец подтвердил получение оплаты по ордеру #{order_id}!**\n\nСделка успешно завершена.",
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="p2p:back_home")]]
            ),
        )
    except Exception as e:
        LOGGER.error(f"Failed to notify buyer: {e}")


@ROUTER.callback_query(F.data == "p2p:my_orders")
async def p2p_my_orders(callback: types.CallbackQuery) -> None:
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute(
            "SELECT order_id, pair, order_type, amount, price, status FROM p2p_orders WHERE seller_id = ? OR buyer_id = ? ORDER BY order_id DESC",
            (callback.from_user.id, callback.from_user.id),
        ).fetchall()

    if not orders:
        text = "📦 У вас нет активных или завершенных ордеров."
    else:
        text = "📦 **Ваши ордера:**\n\n"
        for o in orders:
            o_id, pair, o_type, amount, price, status = o
            text += f"#{o_id} | {pair} | {o_type} | {amount} ZRL @ {price} | Статус: **{status}**\n"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="p2p:menu")]]
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data == "p2p:back_home")
async def p2p_back_home(callback: types.CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    me = await bot.get_me()
    if callback.message is not None:
        await callback.message.edit_text(
            get_text(user_id, "welcome", limit=FREE_LIMIT),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(me.username, user_id),
        )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()

    token = get_required_env("TELEGRAM_BOT_TOKEN")
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(ROUTER)

    LOGGER.info("Zer0Life Labs AI Bot with P2P Escrow is running...")
    await bot.get_updates(offset=-1)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

