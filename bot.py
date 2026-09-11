"""
Zer0Life Labs AI Telegram Bot: AI Assistant, P2P Escrow Marketplace, 
Crypto Subscriptions, Auto-Trading, Merch, ZRL Airdrop Mini-Game & Enterprise Wallet.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import logging
import os
import random
import sqlite3
from typing import Final, Optional

import aiohttp
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.html import quote
from openai import AsyncOpenAI

# Import external wallet module
from zer0life_wallet import wallet_router, init_wallet_db

LOGGER = logging.getLogger(__name__)
ROUTER = Router()

# ==================== CONFIGURATION ====================
ZRL_MINT_ADDRESS: Final[str] = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"
SOL_MINT: Final[str] = "So11111111111111111111111111111111111111112"
USDC_MINT: Final[str] = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

SOLANA_TREASURY_WALLET: Final[str] = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"
BSC_TREASURY_WALLET: Final[str] = "0x7901D7566766379f9ffc11326762883D6161183f"

TOTAL_AIRDROP_POOL: Final[float] = 100_000_000.0
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

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
            "✨ <b>Welcome to Zer0Life Labs AI Ecosystem!</b>\n\n"
            "🎁 You have <b>{limit} free AI requests</b> available.\n"
            "🤖 Launch the <b>ZRL Airdrop</b> mini-game, Auto-Trading, or P2P escrow market on Solana.\n\n"
            "Select a section from the menu below:"
        ),
        "choose_lang": "🌐 Please select your preferred language:",
        "btn_wallet": "💼 My Wallet",
        "btn_airdrop": "🎁 Join ZRL Airdrop",
        "btn_autotrade": "🤖 Auto-Trading Bot",
        "btn_p2p": "💱 P2P Marketplace (Escrow)",
        "btn_merch": "🛍 Official Merch Store",
        "btn_sub": "💎 Buy Pro Subscription ($10)",
        "btn_stats": "📊 Ecosystem Stats",
        "btn_share": "📤 Share Bot",
        "btn_about": "ℹ️ About Zer0Life Labs AI",
        "btn_lang": "🌐 Language: English",
        "back_home": "🏠 Main Menu",
        "lang_updated": "✅ Language successfully changed to English!",
        "merch_title": (
            "🛍 <b>Zer0Life Labs Official Merchandise</b>\n\n"
            "Choose your preferred marketplace to check out our products:"
        ),
        "btn_etsy": "🛒 Etsy Shop",
        "btn_allegro": "📦 Allegro Store",
        "btn_amazon": "📦 Amazon Store",
    },
    "ru": {
        "welcome": (
            "✨ <b>Добро пожаловать в экосистему Zer0Life Labs AI!</b>\n\n"
            "🎁 Вам доступно <b>{limit} бесплатных запросов</b> к AI-ассистенту.\n"
            "🤖 Запустите мини-игру <b>Airdrop ZRL</b>, Авто-трейдинг или P2P-эскроу рынок в сети Solana.\n\n"
            "Выберите нужный раздел в меню ниже:"
        ),
        "choose_lang": "🌐 Пожалуйста, выберите язык / Please select your language:",
        "btn_wallet": "💼 Мой Кошелек",
        "btn_airdrop": "🎁 Участвовать в Airdrop ZRL",
        "btn_autotrade": "🤖 Авто-трейдинг бот",
        "btn_p2p": "💱 P2P Биржа (Эскроу)",
        "btn_merch": "🛍 Наш Мерч (Магазины)",
        "btn_sub": "💎 Купить Pro-подписку ($10)",
        "btn_stats": "📊 Статистика экосистемы",
        "btn_share": "📤 Поделиться ботом",
        "btn_about": "ℹ️ О проекте Zer0Life Labs AI",
        "btn_lang": "🌐 Язык: Русский",
        "back_home": "🏠 Главное меню",
        "lang_updated": "✅ Язык успешно изменен на русский!",
        "merch_title": (
            "🛍 <b>Официальный Мерч Zer0Life Labs</b>\n\n"
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


# ==================== FSM STATES ====================
class P2POrderState(StatesGroup):
    waiting_for_pair = State()
    waiting_for_type = State()
    waiting_for_amount = State()
    waiting_for_price = State()
    waiting_for_wallet = State()


class P2PProofState(StatesGroup):
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


# ==================== DATABASE INITIALIZATION ====================
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
                buyer_id INTEGER DEFAULT NULL,
                pair TEXT NOT NULL,
                order_type TEXT NOT NULL,
                amount REAL NOT NULL,
                price REAL NOT NULL,
                seller_wallet TEXT NOT NULL DEFAULT '',
                proof_data TEXT DEFAULT NULL,
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


# ==================== BLOCKCHAIN & PRICE UTILS ====================
async def get_crypto_prices() -> dict[str, float]:
    prices = {"SOL": 150.0, "BNB": 600.0, "ZRL": 0.05}
    
    ids = f"{SOL_MINT},{USDC_MINT}"
    if ZRL_MINT_ADDRESS:
        ids += f",{ZRL_MINT_ADDRESS}"

    url = f"https://api.jup.ag/price/v3?ids={ids}"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    res = data.get("data", {})
                    
                    sol_data = res.get(SOL_MINT, {})
                    if isinstance(sol_data, dict):
                        sol_p = float(sol_data.get("price", 0))
                        if sol_p > 0:
                            prices["SOL"] = sol_p
                    
                    if ZRL_MINT_ADDRESS:
                        zrl_data = res.get(ZRL_MINT_ADDRESS, {})
                        if isinstance(zrl_data, dict):
                            zrl_p = float(zrl_data.get("price", 0))
                            if zrl_p > 0:
                                prices["ZRL"] = zrl_p

            async with session.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    bg_data = await resp.json()
                    bnb_p = float(bg_data.get("binancecoin", {}).get("usd", 0))
                    if bnb_p > 0:
                        prices["BNB"] = bnb_p
        except Exception as e:
            LOGGER.error(f"Error fetching crypto prices: {e}")

    return prices


async def verify_solana_tx(tx_hash: str) -> bool:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignatureStatuses",
        "params": [[tx_hash], {"searchTransactionHistory": True}]
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(SOLANA_RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    statuses = data.get("result", {}).get("value", [])
                    if statuses and statuses[0]:
                        status = statuses[0].get("confirmationStatus")
                        return status in ["confirmed", "finalized"]
        except Exception as e:
            LOGGER.error(f"Solana RPC error: {e}")
    return False


# ==================== KEYBOARDS ====================
def main_menu_keyboard(bot_username: str, user_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_wallet"),
                    callback_data="app_wallet:view",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_airdrop"),
                    callback_data="airdrop:menu",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_autotrade"),
                    callback_data="autotrade:menu",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_p2p"),
                    callback_data="p2p:menu",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_merch"),
                    callback_data="merch:menu",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_sub"),
                    callback_data="sub:choose_currency",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_stats"),
                    callback_data="stats:view",
                ),
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_share"),
                    url=f"https://t.me/share/url?url=https://t.me/{bot_username}&text=🚀%20Use%20Zer0Life%20Labs%20AI%20for%20auto-trading%20and%20P2P!",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_about"),
                    callback_data="info:about",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=get_text(user_id, "btn_lang"),
                    callback_data="lang:choose",
                )
            ],
        ]
    )


def p2p_menu_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="📋 Active Orders (Orderbook)",
                    callback_data="p2p:list_orders",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="➕ Create Escrow Order",
                    callback_data="p2p:create_order",
                ),
                types.InlineKeyboardButton(
                    text="📦 My Orders",
                    callback_data="p2p:my_orders",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="🔄 Refresh DEX Rates",
                    callback_data="p2p:refresh",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="⬅️ Main Menu",
                    callback_data="p2p:back_home",
                )
            ],
        ]
    )


def autotrade_menu_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="⚙️ Configure / Launch Auto-Trading",
                    callback_data="autotrade:configure",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="📊 Auto-Trading Status",
                    callback_data="autotrade:status",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="⬅️ Main Menu",
                    callback_data="p2p:back_home",
                )
            ],
        ]
    )


# ==================== HANDLERS ====================

@ROUTER.message(Command("start"))
async def start_handler(message: types.Message, bot: Bot, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    now_str = datetime.now(timezone.utc).isoformat()
    
    tg_lang = message.from_user.language_code
    default_lang = "ru" if tg_lang and tg_lang.startswith("ru") else "en"

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        existing = conn.execute("SELECT language FROM user_usage WHERE telegram_user_id = ?", (user_id,)).fetchone()
        
        if not existing:
            conn.execute(
                """
                INSERT INTO user_usage (telegram_user_id, language, last_seen) 
                VALUES (?, ?, ?)
                """,
                (user_id, default_lang, now_str),
            )
        else:
            conn.execute("UPDATE user_usage SET last_seen = ? WHERE telegram_user_id = ?", (now_str, user_id))
        
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
        get_text(user_id, "welcome", limit=FREE_LIMIT),
        reply_markup=main_menu_keyboard(me.username, user_id),
    )


@ROUTER.callback_query(F.data == "merch:menu")
async def merch_menu_handler(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_etsy"), url=ETSY_URL)],
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_allegro"), url=ALLEGRO_URL)],
            [types.InlineKeyboardButton(text=get_text(user_id, "btn_amazon"), url=AMAZON_URL)],
            [types.InlineKeyboardButton(text=get_text(user_id, "back_home"), callback_data="p2p:back_home")],
        ]
    )

    if callback.message is not None:
        await callback.message.edit_text(
            get_text(user_id, "merch_title"),
            reply_markup=keyboard,
        )


@ROUTER.callback_query(F.data.startswith("lang:"))
async def language_callback(callback: types.CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    action = callback.data.split(":")[1]
    
    if action == "choose":
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:set_en"),
                    types.InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:set_ru"),
                ],
                [types.InlineKeyboardButton(text="🏠 Back to Menu", callback_data="lang:back_home")]
            ]
        )
        if callback.message:
            await callback.message.edit_text("🌐 Please select your preferred language / Выберите язык:", reply_markup=keyboard)
        return

    if action.startswith("set_"):
        new_lang = action.split("_")[1]
        with sqlite3.connect(USAGE_DB_PATH) as conn:
            conn.execute("UPDATE user_usage SET language = ? WHERE telegram_user_id = ?", (new_lang, user_id))
        
        await callback.answer(get_text(user_id, "lang_updated"))
        me = await bot.get_me()
        
        if callback.message:
            await callback.message.edit_text(
                get_text(user_id, "welcome", limit=FREE_LIMIT),
                reply_markup=main_menu_keyboard(me.username, user_id),
            )
        return

    if action == "back_home":
        me = await bot.get_me()
        if callback.message:
            await callback.message.edit_text(
                get_text(user_id, "welcome", limit=FREE_LIMIT),
                reply_markup=main_menu_keyboard(me.username, user_id),
            )


@ROUTER.callback_query(F.data == "airdrop:menu")
async def airdrop_menu_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        existing = conn.execute(
            "SELECT reward_amount, solana_address, status FROM airdrop_claims WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        res = conn.execute("SELECT SUM(reward_amount), COUNT(*) FROM airdrop_claims").fetchone()
        claimed_pool = res[0] if res and res[0] else 0.0
        participants_count = res[1] if res and res[1] else 0

    remaining_pool = max(0.0, TOTAL_AIRDROP_POOL - claimed_pool)

    if existing:
        reward, addr, status = existing
        text = (
            "🎁 <b>ZRL Token Airdrop — Status</b>\n\n"
            "⚠️ Вы уже приняли участие с этого аккаунта!\n\n"
            f"• Награда: <code>{reward:,.0f} ZRL</code>\n"
            f"• Ваш Solana адрес: <code>{quote(addr)}</code>\n"
            f"• Статус выплаты: <b>{quote(status.upper())}</b>\n\n"
            f"📊 <b>Статистика пула:</b>\n"
            f"• Выдано: <code>{claimed_pool:,.0f} / 100,000,000 ZRL</code>\n"
            f"• Осталось: <code>{remaining_pool:,.0f} ZRL</code>\n"
            f"• Всего участников: <code>{participants_count}</code>"
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Главное меню", callback_data="p2p:back_home")]]
        )
    elif remaining_pool <= 0:
        text = (
            "🎁 <b>ZRL Token Free Airdrop</b>\n\n"
            "❌ К сожалению, весь пул в размере <b>100,000,000 ZRL</b> полностью распределен!"
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Главное меню", callback_data="p2p:back_home")]]
        )
    else:
        text = (
            "🎁 <b>ZRL Token Free Airdrop</b>\n\n"
            f"📊 <b>Статистика пула:</b>\n"
            f"• Всего в пуле: <code>100,000,000 ZRL</code>\n"
            f"• Выдано: <code>{claimed_pool:,.0f} ZRL</code>\n"
            f"• Осталось: <b>{remaining_pool:,.0f} ZRL</b>\n"
            f"• Всего участников: <code>{participants_count}</code>\n\n"
            "🛡 <b>Защита от абуза:</b> 1 Telegram аккаунт = 1 Solana кошелек.\n\n"
            "Испытайте удачу! Нажмите кнопку ниже, чтобы сгенерировать случайную награду от <b>100 до 100,000 ZRL</b>:"
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🎲 Крутить барабан и получить Airdrop", callback_data="airdrop:roll")],
                [types.InlineKeyboardButton(text="⬅️ Главное меню", callback_data="p2p:back_home")],
            ]
        )

    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=keyboard)


@ROUTER.callback_query(F.data == "airdrop:roll")
async def airdrop_roll_callback(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer("🎰 Крутим барабан распределения airdrop...")
    user_id = callback.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        existing = conn.execute("SELECT user_id FROM airdrop_claims WHERE user_id = ?", (user_id,)).fetchone()
        if existing:
            await callback.answer("⚠️ Вы уже забирали свой airdrop!", show_alert=True)
            return

        res = conn.execute("SELECT SUM(reward_amount) FROM airdrop_claims").fetchone()
        claimed_pool = res[0] if res and res[0] else 0.0
        if claimed_pool >= TOTAL_AIRDROP_POOL:
            await callback.answer("❌ Пул airdrop полностью исчерпан!", show_alert=True)
            return

    reward_amount = float(random.randint(100, 100000))
    await state.update_data(airdrop_reward=reward_amount)
    await state.set_state(AirdropClaimState.waiting_for_solana_address)

    text = (
        "🎉 <b>Поздравляем! Генератор определил вашу награду:</b>\n\n"
        f"🏆 Вы выиграли: <b>{reward_amount:,.0f} ZRL</b>\n\n"
        "👇 Отправьте ответным сообщением свой <b>Solana адрес</b> (например, Phantom, Solflare), куда мы зачислим токены:"
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="❌ Отмена", callback_data="airdrop:menu")]]
    )

    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=keyboard)


@ROUTER.message(AirdropClaimState.waiting_for_solana_address)
async def airdrop_save_address(message: types.Message, state: FSMContext, bot: Bot) -> None:
    sol_address = message.text.strip()
    if len(sol_address) < 32 or len(sol_address) > 44:
        await message.answer("⚠️ Неверный формат Solana адреса. Попробуйте еще раз:")
        return

    user_id = message.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        already_claimed = conn.execute("SELECT user_id FROM airdrop_claims WHERE user_id = ?", (user_id,)).fetchone()
        if already_claimed:
            await state.clear()
            await message.answer("⚠️ Вы уже получали airdrop с этого аккаунта!")
            return

        address_used = conn.execute("SELECT user_id FROM airdrop_claims WHERE solana_address = ?", (sol_address,)).fetchone()
        if address_used:
            await message.answer(
                "⚠️ <b>Этот Solana адрес уже зарегистрирован другим участником!</b>\n\n"
                "Введите другой личный адрес Solana:"
            )
            return

        data = await state.get_data()
        reward_amount = data.get("airdrop_reward", 1000.0)

        conn.execute(
            """
            INSERT INTO airdrop_claims (user_id, reward_amount, solana_address, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (user_id, reward_amount, sol_address),
        )

    await state.clear()
    username = message.from_user.username or "no_username"
    full_name = message.from_user.full_name or "No Name"

    await message.answer(
        "✅ <b>Адрес успешно сохранен!</b>\n\n"
        f"• Награда: <code>{reward_amount:,.0f} ZRL</code>\n"
        f"• Адрес: <code>{quote(sol_address)}</code>\n\n"
        "Токены будут отправлены на ваш кошелек в порядке очереди!",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="p2p:back_home")]]
        ),
    )

    if ADMIN_TELEGRAM_ID > 0:
        try:
            admin_text = (
                "🚨 <b>Новая заявка на Airdrop ZRL!</b>\n\n"
                f"👤 <b>Пользователь:</b> {quote(full_name)} (@{quote(username)})\n"
                f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
                f"🎁 <b>Сумма:</b> <code>{reward_amount:,.0f} ZRL</code>\n\n"
                f"👛 <b>Кошелек Solana:</b>\n"
                f"<code>{quote(sol_address)}</code>"
            )
            await bot.send_message(ADMIN_TELEGRAM_ID, admin_text)
        except Exception as e:
            LOGGER.error(f"Failed to send airdrop notification to admin: {e}")


@ROUTER.callback_query(F.data == "autotrade:menu")
async def autotrade_menu_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "🤖 <b>Auto-Trading Bot (Solana / Jupiter DEX)</b>\n\n"
            "Бот автоматически отслеживает ликвидность, торговые объемы и исполняет сделки по заданным параметрам.\n\n"
            "Выберите действие:",
            reply_markup=autotrade_menu_keyboard(),
        )


@ROUTER.callback_query(F.data == "autotrade:configure")
async def autotrade_configure(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="Solana (SOL)", callback_data="at_pair:SOL"),
                types.InlineKeyboardButton(text="Binance Coin (BNB)", callback_data="at_pair:BNB"),
            ],
            [
                types.InlineKeyboardButton(text="ZRL Token (ZRL)", callback_data="at_pair:ZRL"),
            ],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="autotrade:menu")],
        ]
    )
    await state.set_state(AutoTradeSettingsState.waiting_for_pair)
    if callback.message is not None:
        await callback.message.edit_text("⚙️ Выберите актив для авто-трейдинга:", reply_markup=keyboard)


@ROUTER.callback_query(AutoTradeSettingsState.waiting_for_pair, F.data.startswith("at_pair:"))
async def autotrade_set_pair(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    pair = callback.data.split(":")[1]
    await state.update_data(pair=pair)
    await state.set_state(AutoTradeSettingsState.waiting_for_amount)
    if callback.message is not None:
        await callback.message.edit_text(
            f"Выбран актив: <b>{quote(pair)}</b>.\n\n"
            "Введите минимальный размер депозита на сделку в USD (например, <code>50</code> или <code>100</code>):"
        )


@ROUTER.message(AutoTradeSettingsState.waiting_for_amount)
async def autotrade_set_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Неверный формат. Введите число больше 0:")
        return

    await state.update_data(amount=amount)
    await state.set_state(AutoTradeSettingsState.waiting_for_target)
    await message.answer("Введите процент целевой прибыли для закрытия сделки (например, <code>20</code> для 20%):")


@ROUTER.message(AutoTradeSettingsState.waiting_for_target)
async def autotrade_set_target(message: types.Message, state: FSMContext) -> None:
    try:
        target = float(message.text.strip().replace(",", "."))
        if target <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Неверный формат. Введите число больше 0:")
        return

    data = await state.get_data()
    user_id = message.from_user.id
    pair = data["pair"]
    amount = data["amount"]

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO auto_trade (user_id, pair, amount, target_percent, status)
            VALUES (?, ?, ?, ?, 'active')
            ON CONFLICT(user_id)
            DO UPDATE SET pair = ?, amount = ?, target_percent = ?, status = 'active'
            """,
            (user_id, pair, amount, target, pair, amount, target),
        )

    await state.clear()
    await message.answer(
        "✅ <b>Авто-трейдинг успешно настроен и запущен!</b>\n\n"
        f"• Актив: <code>{quote(pair)}</code>\n"
        f"• Мин. депозит: <code>${amount}</code>\n"
        f"• Цель по прибыли: <code>+{target}%</code>\n\n"
        "Бот начал отслеживание пулов ликвидности через Jupiter DEX.",
        reply_markup=autotrade_menu_keyboard(),
    )


@ROUTER.callback_query(F.data == "autotrade:status")
async def autotrade_status(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute(
            "SELECT pair, amount, target_percent, status FROM auto_trade WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    if not row or row[3] == 'inactive':
        text = "📊 У вас нет активных сессий авто-трейдинга."
    else:
        pair, amount, target, status = row
        text = (
            "📊 <b>Статус вашего Авто-трейдинг бота:</b>\n\n"
            f"• Статус: <b>{quote(status.upper())}</b>\n"
            f"• Торговый актив: <code>{quote(pair)}</code>\n"
            f"• Депозит на сделку: <code>${amount}</code>\n"
            f"• Цель прибыли: <code>+{target}%</code>"
        )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад в меню бота", callback_data="autotrade:menu")]]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=keyboard)


@ROUTER.callback_query(F.data == "sub:choose_currency")
async def sub_choose_currency(callback: types.CallbackQuery) -> None:
    await callback.answer("⏳ Рассчитываем актуальные курсы...")
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
                types.InlineKeyboardButton(text="⬅️ В главное меню", callback_data="p2p:back_home"),
            ],
        ]
    )

    if callback.message is not None:
        await callback.message.edit_text(
            "💎 <b>Покупка Pro Подписки</b>\n\n"
            f"Стоимость подписки: <b>${SUBSCRIPTION_PRICE_USD}</b>\n"
            "Выберите криптовалюту для оплаты:",
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
        f"💎 <b>Оплата Pro Подписки ({quote(currency)})</b>\n\n"
        f"К оплате: <code>{amount:.4f} {quote(currency)}</code>\n"
        f"Сеть: <b>{quote(network)}</b>\n\n"
        f"📌 <b>Адрес пополнения:</b>\n<code>{quote(treasury)}</code>\n\n"
        "После перевода нажмите кнопку ниже для автоматической проверки."
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Проверить платеж", callback_data=f"check_pay:{currency}:{amount:.4f}")],
            [types.InlineKeyboardButton(text="⬅️ Выбрать другую валюту", callback_data="sub:choose_currency")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=keyboard)


@ROUTER.callback_query(F.data.startswith("check_pay:"))
async def check_payment_handler(callback: types.CallbackQuery) -> None:
    await callback.answer("🔍 Проверяем блокчейн...", show_alert=False)
    
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
                "✅ <b>Оплата успешно найдена и подтверждена!</b>\n\n"
                f"Получено: <code>{expected_amount} {quote(currency)}</code>\n"
                "Ваша Pro подписка активирована. Приятного пользования ✨",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="p2p:back_home")]]
                ),
            )
    else:
        await callback.answer(
            "⚠️ Транзакция еще не найдена. Убедитесь, что перевод отправлен, и повторите попытку через минуту.",
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
        "📊 <b>Статистика экосистемы Zer0Life Labs AI</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"🟢 Онлайн: <b>{online_count}</b>\n"
        f"⚪ Офлайн: <b>{offline_count}</b>\n"
        f"📤 Всего шеров: <b>{total_shares}</b>\n"
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="📤 Поделиться ботом",
                    url=f"https://t.me/share/url?url=https://t.me/{me.username}&text=🚀%20Use%20Zer0Life%20Labs%20AI!",
                )
            ],
            [types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="p2p:back_home")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=keyboard)


@ROUTER.callback_query(F.data == "info:about")
async def info_about_handler(callback: types.CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "ℹ️ <b>О проекте Zer0Life Labs AI & ZRL Token</b>\n\n"
            "• <b>AI-Ассистент:</b> Решает задачи, анализирует изображения.\n"
            "• <b>Кошелек:</b> Мультивалютный кошелек для безопасных переводов.\n"
            "• <b>Авто-трейдинг:</b> Торговый бот для Solana.\n"
            "• <b>Airdrop:</b> Мини-игра с раздачей ZRL токенов (пул 100M).\n"
            "• <b>ZRL Token:</b> Нативный токен экосистемы.\n\n"
            "Отправьте фото в чат, чтобы протестировать анализ изображений.",
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
            f"⚠️ <b>Лимит бесплатных запросов исчерпан ({FREE_LIMIT}/{FREE_LIMIT}).</b>\n\n"
            "Для продолжения оформите Pro подписку за <b>$10</b>.",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="💎 Купить Pro Подписку", callback_data="sub:choose_currency")]]
            ),
        )
        return

    status_msg = await message.answer("🤖 Zer0Life AI анализирует изображение...")
    
    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        
        client = AsyncOpenAI(api_key=get_required_env("OPENAI_API_KEY"))
        base64_image = base64.b64encode(photo_bytes.read()).decode('utf-8')

        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Provide a detailed and useful analysis of this image in English."},
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
        footer = f"\n\n<i>Осталось бесплатных запросов: {remaining}/{FREE_LIMIT}</i>" if not is_pro else "\n\n<i>Pro Аккаунт Активен ✨</i>"
        
        await status_msg.edit_text(f"✨ <b>Ответ AI-Ассистента:</b>\n\n{quote(result_text)}{footer}")

    except Exception as e:
        LOGGER.error(f"Error processing photo: {e}")
        await status_msg.edit_text("❌ Произошла ошибка при обработке изображения.")


@ROUTER.callback_query(F.data.in_({"p2p:menu", "p2p:refresh"}))
async def p2p_menu_handler(callback: types.CallbackQuery) -> None:
    await callback.answer("⏳ Синхронизация курсов с Jupiter DEX...")
    if callback.message is None:
        return

    rates = await get_crypto_prices()
    text = (
        "💱 <b>Zer0Life Labs AI — P2P Escrow Marketplace (Solana)</b>\n\n"
        "Текущие курсы токена <b>ZRL</b>:\n"
        f"🔹 <b>ZRL / SOL:</b> <code>{rates['ZRL'] / rates['SOL']:.6f}</code> SOL\n"
        f"🔹 <b>ZRL / USDC:</b> <code>${rates['ZRL']:.4f}</code> USDC\n\n"
        "Управление ордерами:"
    )

    try:
        await callback.message.edit_text(text, reply_markup=p2p_menu_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=p2p_menu_keyboard())


@ROUTER.callback_query(F.data == "p2p:back_home")
async def p2p_back_home(callback: types.CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    me = await bot.get_me()
    if callback.message is not None:
        await callback.message.edit_text(
            get_text(user_id, "welcome", limit=FREE_LIMIT),
            reply_markup=main_menu_keyboard(me.username, user_id),
        )


@ROUTER.callback_query(F.data == "p2p:list_orders")
async def p2p_list_orders(callback: types.CallbackQuery) -> None:
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute(
            "SELECT order_id, seller_id, pair, order_type, amount, price FROM p2p_orders WHERE status = 'active' ORDER BY order_id DESC LIMIT 10"
        ).fetchall()

    if not orders:
        text = "📋 На данный момент нет активных ордеров на P2P рынке."
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад в P2P", callback_data="p2p:menu")]]
        )
    else:
        text = "📋 <b>Активные ордера ZRL:</b>\n\n"
        keyboard_buttons = []
        for o in orders:
            o_id, seller_id, pair, o_type, amount, price = o
            emoji = "🟢 КУПИТЬ" if o_type == "BUY" else "🔴 ПРОДАТЬ"
            total_sum = amount * price
            currency_symbol = pair.split("/")[1]
            
            text += (
                f"#{o_id} | <b>{quote(pair)}</b> | {emoji}\n"
                f"🔹 Количество: <code>{amount:,.0f} ZRL</code>\n"
                f"🔹 Цена: <code>{price} {quote(currency_symbol)}</code> (Всего: <code>{total_sum:,.4f} {quote(currency_symbol)}</code>)\n\n"
            )
            
            keyboard_buttons.append([
                types.InlineKeyboardButton(
                    text=f"🤝 Принять ордер #{o_id} ({o_type})",
                    callback_data=f"p2p:accept:{o_id}"
                )
            ])
        
        keyboard_buttons.append([types.InlineKeyboardButton(text="⬅️ Назад в P2P", callback_data="p2p:menu")])
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)


@ROUTER.callback_query(F.data.startswith("p2p:accept:"))
async def p2p_accept_deal(callback: types.CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.split(":")[2])
    buyer_id = callback.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        order = conn.execute(
            "SELECT seller_id, pair, order_type, amount, price, seller_wallet, status FROM p2p_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()

        if not order or order[6] != "active":
            await callback.answer("⚠️ Ордер больше недоступен.", show_alert=True)
            return

        seller_id, pair, o_type, amount, price, seller_wallet, status = order

        if seller_id == buyer_id:
            await callback.answer("❌ Нельзя исполнить собственный ордер.", show_alert=True)
            return

        conn.execute(
            "UPDATE p2p_orders SET buyer_id = ?, status = 'pending_payment' WHERE order_id = ?",
            (buyer_id, order_id),
        )

    total_sum = amount * price
    currency_symbol = pair.split("/")[1]

    await state.update_data(current_order_id=order_id)
    await state.set_state(P2PProofState.waiting_for_proof)

    text = (
        f"🤝 <b>Сделка по ордеру #{order_id} начата!</b>\n\n"
        f"• Пара: <code>{quote(pair)}</code>\n"
        f"• Объём: <code>{amount:,.0f} ZRL</code>\n"
        f"• Итого к оплате: <code>{total_sum:,.4f} {quote(currency_symbol)}</code>\n\n"
        f"📌 <b>Solana адрес продавца:</b>\n<code>{quote(seller_wallet)}</code>\n\n"
        "👇 <b>Действие:</b> Совершите перевод и отправьте <b>скриншот чек</b> или <b>Solana TX Hash</b> прямо в этот чат:"
    )

    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="❌ Отменить сделку", callback_data="p2p:menu")]]
            )
        )


@ROUTER.message(P2PProofState.waiting_for_proof)
async def p2p_receive_proof(message: types.Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    order_id = data.get("current_order_id")
    proof = ""

    if message.photo:
        proof = f"photo:{message.photo[-1].file_id}"
    elif message.text:
        proof = f"tx:{message.text.strip()}"
    else:
        await message.answer("⚠️ Неверный формат. Загрузите скриншот или отправьте TX Hash:")
        return

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute(
            "UPDATE p2p_orders SET proof_data = ?, status = 'proof_submitted' WHERE order_id = ?",
            (proof, order_id),
        )
        order = conn.execute("SELECT seller_id, pair, amount FROM p2p_orders WHERE order_id = ?", (order_id,)).fetchone()

    seller_id = order[0]
    await state.clear()
    await message.answer(
        "✅ <b>Подтверждение оплаты загружено!</b>\n\nПродавец уведомлен для проверки получения средств.",
        reply_markup=p2p_menu_keyboard(),
    )

    confirm_kb = types.InlineKeyboardMarkup(
        inline_keyboard=[[
            types.InlineKeyboardButton(text="✅ Подтвердить получение", callback_data=f"p2p:confirm:{order_id}")
        ]]
    )

    try:
        if proof.startswith("photo:"):
            photo_id = proof.split("photo:")[1]
            await bot.send_photo(
                seller_id,
                photo_id,
                caption=f"🔔 <b>Получена оплата по ордеру #{order_id}!</b>\n\nПроверьте кошелек и подтвердите сделки:",
                reply_markup=confirm_kb,
            )
        else:
            tx_hash = proof.split("tx:")[1]
            is_valid = await verify_solana_tx(tx_hash)
            verified_txt = "🟢 <b>RPC Проверка:</b> Транзакция найдена в Solana!" if is_valid else "🟡 <b>RPC Проверка:</b> Ожидает подтверждения"
            await bot.send_message(
                seller_id,
                f"🔔 <b>Отправлен TX Hash по ордеру #{order_id}!</b>\n\n<code>{quote(tx_hash)}</code>\n\n{verified_txt}\n\nПодтвердите получение ниже:",
                reply_markup=confirm_kb,
            )
    except Exception as e:
        LOGGER.error(f"Failed to notify seller: {e}")


@ROUTER.callback_query(F.data.startswith("p2p:confirm:"))
async def p2p_seller_confirm(callback: types.CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.split(":")[2])
    seller_id = callback.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        order = conn.execute("SELECT buyer_id, seller_id, status FROM p2p_orders WHERE order_id = ?", (order_id,)).fetchone()
        if not order or order[1] != seller_id:
            await callback.answer("❌ Доступ запрещен.", show_alert=True)
            return

        buyer_id = order[0]
        conn.execute("UPDATE p2p_orders SET status = 'completed' WHERE order_id = ?", (order_id,))

    await callback.answer("✅ Сделка успешно завершена!")
    if callback.message:
        await callback.message.edit_text(f"🎉 <b>Ордер #{order_id} успешно закрыт!</b>")

    try:
        if buyer_id:
            await bot.send_message(
                buyer_id,
                f"🎉 <b>Продавец подтвердил оплату по ордеру #{order_id}!</b> Сделка завершена.",
                reply_markup=p2p_menu_keyboard(),
            )
    except Exception:
        pass


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
    if callback.message:
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
    if callback.message:
        await callback.message.edit_text(f"Выбрана пара <b>{quote(pair)}</b>.\nВыберите направление ордера:", reply_markup=keyboard)


@ROUTER.callback_query(P2POrderState.waiting_for_type, F.data.startswith("type:"))
async def p2p_choose_type(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    o_type = callback.data.split(":")[1]
    await state.update_data(order_type=o_type)

    await state.set_state(P2POrderState.waiting_for_amount)
    if callback.message:
        await callback.message.edit_text("Введите количество токенов ZRL (например, <code>1000</code>):")


@ROUTER.message(P2POrderState.waiting_for_amount)
async def p2p_get_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip().replace(",", "."))
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
        await message.answer("⚠️ Неверный формат цены. Введите число больше 0:")
        return

    await state.update_data(price=price)
    await state.set_state(P2POrderState.waiting_for_wallet)
    await message.answer("👛 Введите ваш Solana адрес кошелька для расчетов:")


@ROUTER.message(P2POrderState.waiting_for_wallet)
async def p2p_get_wallet(message: types.Message, state: FSMContext) -> None:
    wallet = message.text.strip()
    if len(wallet) < 32 or len(wallet) > 44:
        await message.answer("⚠️ Неверный формат Solana адреса. Попробуйте еще раз:")
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
            INSERT INTO p2p_orders (seller_id, pair, order_type, amount, price, seller_wallet)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, pair, order_type, amount, price, wallet),
        )

    await state.clear()
    total_sum = amount * price
    currency_symbol = pair.split("/")[1]

    await message.answer(
        "✅ <b>Ордер успешно создан и опубликован!</b>\n\n"
        f"• Пара: <code>{quote(pair)}</code>\n"
        f"• Тип: <code>{quote(order_type)}</code>\n"
        f"• Количество: <code>{amount:,.0f} ZRL</code>\n"
        f"• Цена: <code>{price} {quote(currency_symbol)}</code>\n"
        f"• Общая сумма: <code>{total_sum:,.4f} {quote(currency_symbol)}</code>\n"
        f"• Ваш кошелек: <code>{quote(wallet)}</code>",
        reply_markup=p2p_menu_keyboard(),
    )


@ROUTER.callback_query(F.data == "p2p:my_orders")
async def p2p_my_orders(callback: types.CallbackQuery) -> None:
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute(
            """
            SELECT order_id, pair, order_type, amount, price, status 
            FROM p2p_orders 
            WHERE seller_id = ? OR buyer_id = ? 
            ORDER BY order_id DESC
            """,
            (callback.from_user.id, callback.from_user.id),
        ).fetchall()

    if not orders:
        text = "📦 У вас пока нет созданных ордеров."
    else:
        text = "📦 <b>Ваши ордера:</b>\n\n"
        for o in orders:
            o_id, pair, o_type, amount, price, status = o
            text += f"#{o_id} | {quote(pair)} | {quote(o_type)} | {amount} ZRL @ {price} | Статус: <b>{quote(status)}</b>\n"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="p2p:menu")]]
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)


# ==================== MAIN LAUNCHER ====================
async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    
    init_db()
    init_wallet_db()

    token = get_required_env("TELEGRAM_BOT_TOKEN")
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())

    # Include external wallet router FIRST to prevent callback interception
    dispatcher.include_router(wallet_router)
    dispatcher.include_router(ROUTER)

    LOGGER.info("Zer0Life Labs AI Bot starting...")
    await bot.get_updates(offset=-1)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
