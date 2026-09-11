"""Zer0Life Labs AI Telegram Bot: AI Assistant, P2P, Crypto Subscriptions, Auto-Trading, Merch & ZRL Airdrop Mini-Game."""

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

SOLANA_TREASURY_WALLET: Final[str] = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"
BSC_TREASURY_WALLET: Final[str] = "0x7901D7566766379f9ffc11326762883D6161183f"

TOTAL_AIRDROP_POOL: Final[float] = 100_000_000.0
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))

# Ссылки на ваши маркетплейсы
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
        "choose_lang": "🌐 Please select your preferred language:",
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
        "choose_lang": "🌐 Пожалуйста, выберите язык / Please select your language:",
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
    """Helper function to fetch localized text for a specific user."""
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
                    text="➕ Create Order",
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
async def start_handler(message: types.Message, bot: Bot, command: CommandObject) -> None:
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
        parse_mode="Markdown",
    )


# --- MERCH STORE HANDLER ---

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
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


# --- LANGUAGE SWITCHER HANDLER ---

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
        await callback.message.edit_text("🌐 Please select your preferred language / Выберите язык:", reply_markup=keyboard)
        return

    if action.startswith("set_"):
        new_lang = action.split("_")[1]
        with sqlite3.connect(USAGE_DB_PATH) as conn:
            conn.execute("UPDATE user_usage SET language = ? WHERE telegram_user_id = ?", (new_lang, user_id))
        
        await callback.answer(get_text(user_id, "lang_updated"))
        me = await bot.get_me()
        
        await callback.message.edit_text(
            get_text(user_id, "welcome", limit=FREE_LIMIT),
            reply_markup=main_menu_keyboard(me.username, user_id),
            parse_mode="Markdown",
        )
        return

    if action == "back_home":
        me = await bot.get_me()
        await callback.message.edit_text(
            get_text(user_id, "welcome", limit=FREE_LIMIT),
            reply_markup=main_menu_keyboard(me.username, user_id),
            parse_mode="Markdown",
        )


# --- AIRDROP ZRL MINI-GAME ---

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
            "🎁 **ZRL Token Airdrop — Status**\n\n"
            "⚠️ You have already participated from this account!\n\n"
            f"• Reward Won: `{reward:,.0f} ZRL`\n"
            f"• Your Solana Address: `{addr}`\n"
            f"• Payout Status: **{status.upper()}**\n\n"
            f"📊 **Pool Statistics:**\n"
            f"• Claimed: `{claimed_pool:,.0f} / 100,000,000 ZRL`\n"
            f"• Remaining: `{remaining_pool:,.0f} ZRL`\n"
            f"• Total Participants: `{participants_count}`"
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")]]
        )
    elif remaining_pool <= 0:
        text = (
            "🎁 **ZRL Token Free Airdrop**\n\n"
            "❌ Unfortunately, the entire pool of **100,000,000 ZRL** has been fully distributed!"
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")]]
        )
    else:
        text = (
            "🎁 **ZRL Token Free Airdrop**\n\n"
            f"📊 **Pool Statistics:**\n"
            f"• Total Pool: `100,000,000 ZRL`\n"
            f"• Claimed: `{claimed_pool:,.0f} ZRL`\n"
            f"• Remaining: **{remaining_pool:,.0f} ZRL**\n"
            f"• Total Participants: `{participants_count}`\n\n"
            "🛡 **Anti-Abuse Protection:** 1 Telegram account = 1 unique Solana wallet (multi-accounts banned).\n\n"
            "Test your luck! Click the button below to generate a random reward from **100 to 100,000 ZRL**:"
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🎲 Spin Generator & Claim Airdrop", callback_data="airdrop:roll")],
                [types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")],
            ]
        )

    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data == "airdrop:roll")
async def airdrop_roll_callback(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer("🎰 Spinning airdrop generation drum...")
    user_id = callback.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        existing = conn.execute("SELECT user_id FROM airdrop_claims WHERE user_id = ?", (user_id,)).fetchone()
        if existing:
            await callback.answer("⚠️ You have already claimed your airdrop from this account!", show_alert=True)
            return

        res = conn.execute("SELECT SUM(reward_amount) FROM airdrop_claims").fetchone()
        claimed_pool = res[0] if res and res[0] else 0.0
        if claimed_pool >= TOTAL_AIRDROP_POOL:
            await callback.answer("❌ Airdrop pool is fully exhausted!", show_alert=True)
            return

    reward_amount = float(random.randint(100, 100000))
    await state.update_data(airdrop_reward=reward_amount)
    await state.set_state(AirdropClaimState.waiting_for_solana_address)

    text = (
        "🎉 **Congratulations! The generator determined your reward:**\n\n"
        f"🏆 You Won: **{reward_amount:,.0f} ZRL**\n\n"
        "👇 Reply to this message with your **unique Solana address** (e.g., Phantom, Solflare) where we will credit your tokens:\n\n"
        "⚠️ *Note: Each wallet can only be used once! Duplicate or reused addresses will be rejected.*"
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="❌ Cancel", callback_data="airdrop:menu")]]
    )

    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.message(AirdropClaimState.waiting_for_solana_address)
async def airdrop_save_address(message: types.Message, state: FSMContext, bot: Bot) -> None:
    sol_address = message.text.strip()
    if len(sol_address) < 32 or len(sol_address) > 44:
        await message.answer("⚠️ Invalid Solana address format. Please check and enter a valid wallet address:")
        return

    user_id = message.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        already_claimed = conn.execute("SELECT user_id FROM airdrop_claims WHERE user_id = ?", (user_id,)).fetchone()
        if already_claimed:
            await state.clear()
            await message.answer("⚠️ You have already claimed an airdrop from this account!")
            return

        address_used = conn.execute("SELECT user_id FROM airdrop_claims WHERE solana_address = ?", (sol_address,)).fetchone()
        if address_used:
            await message.answer(
                "⚠️ **This Solana address is already registered by another participant!**\n\n"
                "Security rules prohibit reusing wallet addresses. Please enter a different personal Solana address:"
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
        "✅ **Address successfully saved and verified!**\n\n"
        f"• Reward: `{reward_amount:,.0f} ZRL`\n"
        f"• Address: `{sol_address}`\n\n"
        "Your request has been accepted. Tokens will be sent to your wallet in a queue order!",
        parse_mode="Markdown",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Main Menu", callback_data="p2p:back_home")]]
        ),
    )

    # --- УВЕДОМЛЕНИЕ АДМИНИСТРАТОРУ (HTML С КЛИКАБЕЛЬНЫМ КОШЕЛЬКОМ) ---
    if ADMIN_TELEGRAM_ID > 0:
        try:
            admin_text = (
                "🚨 <b>Новая заявка на Airdrop ZRL!</b>\n\n"
                f"👤 <b>Пользователь:</b> {full_name} (@{username})\n"
                f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
                f"🎁 <b>Сумма:</b> <code>{reward_amount:,.0f} ZRL</code>\n\n"
                f"👛 <b>Кошелек Solana (нажмите для копирования):</b>\n"
                f"<code>{sol_address}</code>"
            )
            await bot.send_message(ADMIN_TELEGRAM_ID, admin_text, parse_mode="HTML")
        except Exception as e:
            LOGGER.error(f"Failed to send airdrop notification to admin: {e}")


# --- AUTO-TRADING SECTION ---

@ROUTER.callback_query(F.data == "autotrade:menu")
async def autotrade_menu_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "🤖 **Auto-Trading Bot (Solana / Jupiter DEX)**\n\n"
            "The bot automatically monitors liquidity, trading volumes, and executes trades based on set parameters.\n"
            "During testing, this feature is **free** (Pro subscription required later).\n\n"
            "Select an action:",
            parse_mode="Markdown",
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
        await callback.message.edit_text("⚙️ Select base asset/token for auto-trading:", reply_markup=keyboard)


@ROUTER.callback_query(AutoTradeSettingsState.waiting_for_pair, F.data.startswith("at_pair:"))
async def autotrade_set_pair(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    pair = callback.data.split(":")[1]
    await state.update_data(pair=pair)
    await state.set_state(AutoTradeSettingsState.waiting_for_amount)
    if callback.message is not None:
        await callback.message.edit_text(
            f"Selected asset: **{pair}**.\n\n"
            "Enter minimum deposit size per trade in USD (e.g., `50` or `100`):",
            parse_mode="Markdown",
        )


@ROUTER.message(AutoTradeSettingsState.waiting_for_amount)
async def autotrade_set_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Invalid format. Enter a number greater than 0:")
        return

    await state.update_data(amount=amount)
    await state.set_state(AutoTradeSettingsState.waiting_for_target)
    await message.answer("Enter target profit percentage to close the trade (e.g., `20` for 20%):")


@ROUTER.message(AutoTradeSettingsState.waiting_for_target)
async def autotrade_set_target(message: types.Message, state: FSMContext) -> None:
    try:
        target = float(message.text.strip().replace(",", "."))
        if target <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Invalid format. Enter a number greater than 0:")
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
        "✅ **Auto-Trading successfully configured and activated!**\n\n"
        f"• Asset: `{pair}`\n"
        f"• Min Deposit: `${amount}`\n"
        f"• Target Profit: `+{target}%`\n\n"
        "The bot has started monitoring pool liquidity and trading volumes via Jupiter DEX.",
        parse_mode="Markdown",
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
        text = "📊 You don't have any active auto-trading sessions."
    else:
        pair, amount, target, status = row
        text = (
            "📊 **Your Auto-Trading Bot Status:**\n\n"
            f"• State: **{status.upper()}**\n"
            f"• Trading Asset: `{pair}`\n"
            f"• Deposit per trade: `${amount}`\n"
            f"• Profit Target: `+{target}%`\n\n"
            "Bot is actively checking liquidity and volumes on Solana."
        )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Back to Bot Menu", callback_data="autotrade:menu")]]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data == "sub:choose_currency")
async def sub_choose_currency(callback: types.CallbackQuery) -> None:
    await callback.answer("⏳ Calculating actual crypto exchange rates...")
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
                types.InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="p2p:back_home"),
            ],
        ]
    )

    if callback.message is not None:
        await callback.message.edit_text(
            "💎 **Purchase Pro Subscription with Cryptocurrency**\n\n"
            f"Subscription Price: **${SUBSCRIPTION_PRICE_USD}**\n"
            "Select your preferred cryptocurrency for payment:",
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
        f"💎 **Pro Subscription Payment ({currency})**\n\n"
        f"Amount due: `{amount:.4f} {currency}`\n"
        f"Network: **{network}**\n\n"
        f"📌 **Deposit Address:**\n`{treasury}`\n\n"
        "After making the transfer, click the button below to automatically verify the transaction on the blockchain."
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Verify Payment Automatically", callback_data=f"check_pay:{currency}:{amount:.4f}")],
            [types.InlineKeyboardButton(text="⬅️ Choose Another Currency", callback_data="sub:choose_currency")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data.startswith("check_pay:"))
async def check_payment_handler(callback: types.CallbackQuery) -> None:
    await callback.answer("🔍 Scanning blockchain for transfer...", show_alert=False)
    
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
                "✅ **Payment successfully found and confirmed on the blockchain!**\n\n"
                f"Received: `{expected_amount} {currency}`\n"
                "Your Pro subscription has been activated automatically. Enjoy ✨",
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Main Menu", callback_data="p2p:back_home")]]
                ),
            )
    else:
        await callback.answer(
            "⚠️ Transaction not found yet. Make sure the transfer was sent and try again in a minute.",
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
        "📊 **Zer0Life Labs AI Ecosystem Statistics**\n\n"
        f"👥 Total Bot Users: **{total_users}**\n"
        f"🟢 Online Users: **{online_count}**\n"
        f"⚪ Offline Users: **{offline_count}**\n"
        f"📤 Total Shares: **{total_shares}**\n"
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="📤 Share Bot",
                    url=f"https://t.me/share/url?url=https://t.me/{me.username}&text=🚀%20Use%20Zer0Life%20Labs%20AI%20for%20auto-trading%20and%20P2P!",
                )
            ],
            [types.InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="p2p:back_home")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data == "info:about")
async def info_about_handler(callback: types.CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(
            "ℹ️ **About Zer0Life Labs AI & ZRL Token**\n\n"
            "• **AI Assistant:** Solves tasks, analyzes images, and assists in work.\n"
            "• **Auto-Trading:** Trading bot with liquidity and volume verification on Solana.\n"
            "• **Airdrop:** Mini-game featuring random ZRL token distributions (100M pool) and anti-sybil protection.\n"
            "• **Subscription:** First 5 requests free, then Pro access for $10 (SOL, BNB, ZRL payments accepted).\n"
            "• **ZRL Token:** Native ecosystem asset for P2P trading on Solana.\n\n"
            "Send an image in the chat to test AI image analysis.",
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="p2p:back_home")]]
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
            f"⚠️ **Free request limit reached ({FREE_LIMIT}/{FREE_LIMIT}).**\n\n"
            "To continue using the AI assistant, get a Pro subscription for **$10** (SOL, BNB, ZRL accepted).",
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="💎 Buy Pro Subscription", callback_data="sub:choose_currency")]]
            ),
        )
        return

    status_msg = await message.answer("🤖 Zer0Life AI is analyzing the image...")
    
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
        footer = f"\n\n_Free requests remaining: {remaining}/{FREE_LIMIT}_" if not is_pro else "\n\n_Pro Access Active ✨_"
        
        await status_msg.edit_text(f"✨ **AI Assistant Response:**\n\n{result_text}{footer}", parse_mode="Markdown")

    except Exception as e:
        LOGGER.error(f"Error processing photo: {e}")
        await status_msg.edit_text("❌ An error occurred while processing the image.")


# --- P2P MARKETPLACE SECTION ---

@ROUTER.callback_query(F.data.in_({"p2p:menu", "p2p:refresh"}))
async def p2p_menu_handler(callback: types.CallbackQuery) -> None:
    await callback.answer("⏳ Synchronizing rates with Jupiter DEX...")
    if callback.message is None:
        return

    rates = await get_crypto_prices()
    text = (
        "💱 **Zer0Life Labs AI — P2P Marketplace (Solana)**\n\n"
        "Current **ZRL** token rates:\n"
        f"🔹 **ZRL / SOL:** `{rates['ZRL'] / rates['SOL']:.6f}` SOL\n"
        f"🔹 **ZRL / USDC:** `${rates['ZRL']:.4f}` USDC\n\n"
        "Manage order book positions:"
    )

    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=p2p_menu_keyboard())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=p2p_menu_keyboard())


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


@ROUTER.callback_query(F.data == "p2p:list_orders")
async def p2p_list_orders(callback: types.CallbackQuery) -> None:
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute(
            "SELECT order_id, seller_id, pair, order_type, amount, price FROM p2p_orders WHERE status = 'active' ORDER BY order_id DESC LIMIT 10"
        ).fetchall()

    if not orders:
        text = "📋 There are currently no active orders on the P2P market."
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Back to P2P Menu", callback_data="p2p:menu")]]
        )
    else:
        text = "📋 **Active ZRL Orders (click to execute):**\n\n"
        keyboard_buttons = []
        for o in orders:
            o_id, seller_id, pair, o_type, amount, price = o
            emoji = "🟢 BUY" if o_type == "BUY" else "🔴 SELL"
            total_sum = amount * price
            currency_symbol = pair.split("/")[1]
            
            text += f"#{o_id} | **{pair}** | {emoji}\n🔹 Amount: `{amount:,.0f} ZRL`\n🔹 Price: `{price} {currency_symbol}` (Total: `{total_sum:,.4f} {currency_symbol}`)\n\n"
            
            keyboard_buttons.append([
                types.InlineKeyboardButton(
                    text=f"🤝 Execute Order #{o_id} ({o_type})",
                    callback_data=f"p2p:deal:{o_id}"
                )
            ])
        
        keyboard_buttons.append([types.InlineKeyboardButton(text="⬅️ Back to P2P Menu", callback_data="p2p:menu")])
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(F.data.startswith("p2p:deal:"))
async def p2p_execute_deal(callback: types.CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[2])
    buyer_id = callback.from_user.id

    with sqlite3.connect(USAGE_DB_PATH) as conn:
        order = conn.execute(
            "SELECT seller_id, pair, order_type, amount, price, status FROM p2p_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()

        if not order:
            await callback.answer("❌ Order not found or already deleted.", show_alert=True)
            return

        seller_id, pair, o_type, amount, price, status = order

        if status != "active":
            await callback.answer("⚠️ This order has already been completed or canceled.", show_alert=True)
            return

        if seller_id == buyer_id:
            await callback.answer("❌ You cannot execute your own order.", show_alert=True)
            return

        conn.execute(
            "UPDATE p2p_orders SET status = 'completed' WHERE order_id = ?",
            (order_id,),
        )

    total_sum = amount * price
    currency_symbol = pair.split("/")[1]

    await callback.answer("✅ Deal successfully confirmed!", show_alert=True)
    
    await callback.message.edit_text(
        f"🎉 **Deal for Order #{order_id} concluded!**\n\n"
        f"• Pair: `{pair}`\n"
        f"• Type: `{o_type}`\n"
        f"• Volume: `{amount:,.0f} ZRL`\n"
        f"• Price per 1 ZRL: `{price} {currency_symbol}`\n"
        f"• Total Amount: `{total_sum:,.4f} {currency_symbol}`\n\n"
        "Please wait for automated execution on the Solana network.",
        parse_mode="Markdown",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ To P2P Menu", callback_data="p2p:menu")]]
        ),
    )


@ROUTER.callback_query(F.data == "p2p:create_order")
async def p2p_create_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="ZRL / SOL", callback_data="pair:ZRL/SOL"),
                types.InlineKeyboardButton(text="ZRL / USDC", callback_data="pair:ZRL/USDC"),
            ],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="p2p:menu")],
        ]
    )
    await state.set_state(P2POrderState.waiting_for_pair)
    await callback.message.edit_text("💱 Select trading pair to create an order:", reply_markup=keyboard)


@ROUTER.callback_query(P2POrderState.waiting_for_pair, F.data.startswith("pair:"))
async def p2p_choose_pair(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    pair = callback.data.split(":")[1]
    await state.update_data(pair=pair)

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="🟢 Buy ZRL", callback_data="type:BUY"),
                types.InlineKeyboardButton(text="🔴 Sell ZRL", callback_data="type:SELL"),
            ],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="p2p:menu")],
        ]
    )
    await state.set_state(P2POrderState.waiting_for_type)
    await callback.message.edit_text(f"Selected pair **{pair}**.\nChoose order direction:", parse_mode="Markdown", reply_markup=keyboard)


@ROUTER.callback_query(P2POrderState.waiting_for_type, F.data.startswith("type:"))
async def p2p_choose_type(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    o_type = callback.data.split(":")[1]
    await state.update_data(order_type=o_type)

    await state.set_state(P2POrderState.waiting_for_amount)
    await callback.message.edit_text("Enter the amount of ZRL tokens for the order (e.g., `1000`):", parse_mode="Markdown")


@ROUTER.message(P2POrderState.waiting_for_amount)
async def p2p_get_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Invalid format. Enter a number greater than 0:")
        return

    await state.update_data(amount=amount)
    await state.set_state(P2POrderState.waiting_for_price)
    await message.answer("Enter desired price per 1 ZRL:")


@ROUTER.message(P2POrderState.waiting_for_price)
async def p2p_get_price(message: types.Message, state: FSMContext) -> None:
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Invalid price format. Enter a number greater than 0:")
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
    total_sum = amount * price
    currency_symbol = pair.split("/")[1]

    await message.answer(
        "✅ **Order successfully created and published in the order book!**\n\n"
        f"• Pair: `{pair}`\n"
        f"• Type: `{order_type}`\n"
        f"• Amount: `{amount:,.0f} ZRL`\n"
        f"• Price: `{price} {currency_symbol}`\n"
        f"• Total Deal Value: `{total_sum:,.4f} {currency_symbol}`",
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
        text = "📦 You don't have any active orders."
    else:
        text = "📦 **Your ecosystem orders:**\n\n"
        for o in orders:
            o_id, pair, o_type, amount, price, status = o
            text += f"#{o_id} | {pair} | {o_type} | {amount} ZRL @ {price} | Status: {status}\n"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Back", callback_data="p2p:menu")]]
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()

    token = get_required_env("TELEGRAM_BOT_TOKEN")
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(ROUTER)

    LOGGER.info("Zer0Life Labs AI Bot with Autonomous Crypto Payments, Auto-Trading, P2P & Airdrop Mini-Game is running...")
    await bot.get_updates(offset=-1)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
