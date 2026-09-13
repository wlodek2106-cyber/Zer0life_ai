import os
import sys
import logging
import sqlite3
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Zer0LifeLabsBot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
USAGE_DB_PATH = "zer0life_bot.db"
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "123456789"))

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
ROUTER = Router()
dp.include_router(ROUTER)

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "en": {
        "welcome": (
            "🚀 **Welcome to Zer0Life Labs AI Hub, {name}!**\n\n"
            "Your decentralized gateway for AI tools, P2P marketplace, crypto auto-trading, "
            "merch catalog, and exclusive ZRL ecosystem rewards.\n\n"
            "Select an option from the menu below:"
        ),
        "btn_airdrop": "🎁 ZRL Airdrop & Mini-Game",
        "btn_farming": "🌾 ZRL Farming & Staking",
        "btn_autotrade": "🤖 Crypto Auto-Trading",
        "btn_p2p": "🤝 P2P Escrow Market",
        "btn_merch": "👕 Zer0Life Merch Store",
        "btn_sub": "💎 PRO Crypto Subscriptions",
        "btn_wallet": "🔗 Web3 Wallet Connector",
        "btn_stats": "📊 My Account & Stats",
        "btn_share": "📢 Share Bot",
        "btn_about": "ℹ️ About Zer0Life Labs",
        "btn_lang": "🌐 Change Language / Język",
        "farming_title": (
            "🌾 **Zer0Life ZRL Farming & Staking Pool**\n\n"
            "Lock your ZRL tokens to earn high-yield passive rewards.\n"
            "• Current APY: `45%`\n"
            "• Staked Balance: `{staked:,.2f} ZRL`\n"
            "• Unclaimed Rewards: `{reward:,.2f} ZRL`\n"
        ),
        "about_text": (
            "ℹ️ **About Zer0Life Labs**\n\n"
            "Zer0Life is an independent streetwear brand and Web3 ecosystem project "
            "focusing on automated crypto assets, decentralized P2P trading, and cutting-edge apparel.\n\n"
            "• Official Brand: `zer0life_supply`\n"
            "• Ecosystem Token: `ZRL`\n"
        ),
        "lang_changed": "✅ Language successfully changed to English!",
        "p2p_title": "🤝 **Zer0Life P2P Escrow Marketplace**\n\nSelect an option:",
        "merch_title": "👕 **Zer0Life Streetwear Catalog**\n\nExplore our latest drops:",
    },
    "ru": {
        "welcome": (
            "🚀 **Добро пожаловать в хаб Zer0Life Labs AI, {name}!**\n\n"
            "Ваш децентрализованный шлюз для работы с ИИ, P2P-маркетплейса, крипто-автотрейдинга, "
            "магазина мерча и эксклюзивных наград экосистемы ZRL.\n\n"
            "Выберите нужный раздел в меню ниже:"
        ),
        "btn_airdrop": "🎁 ZRL Эйрдроп и мини-игра",
        "btn_farming": "🌾 Фарминг и стейкинг ZRL",
        "btn_autotrade": "🤖 Крипто-автотрейдинг",
        "btn_p2p": "🤝 P2P Эскроу Маркетплейс",
        "btn_merch": "👕 Магазин мерча Zer0Life",
        "btn_sub": "💎 PRO Крипто-подписки",
        "btn_wallet": "🔗 Подключить Web3 Кошелек",
        "btn_stats": "📊 Мой аккаунт и статистика",
        "btn_share": "📢 Поделиться ботом",
        "btn_about": "ℹ️ О проекте Zer0Life Labs",
        "btn_lang": "🌐 Сменить язык / Language",
        "farming_title": (
            "🌾 **Пул фарминга и стейкинга ZRL Zer0Life**\n\n"
            "Блокируйте токены ZRL для получения пассивного дохода.\n"
            "• Текущий APY: `45%`\n"
            "• В стейкинге: `{staked:,.2f} ZRL`\n"
            "• Доступно к сбору: `{reward:,.2f} ZRL`\n"
        ),
        "about_text": (
            "ℹ️ **О проекте Zer0Life Labs**\n\n"
            "Zer0Life — это независимый бренд уличной одежды и Web3-экосистема, "
            "объединяющая автоматизированные криптоактивы, P2P-торговлю и современный дизайн одежды.\n\n"
            "• Бренд: `zer0life_supply`\n"
            "• Токен экосистемы: `ZRL`\n"
        ),
        "lang_changed": "✅ Язык успешно изменен на русский!",
        "p2p_title": "🤝 **P2P Эскроу Маркетплейс Zer0Life**\n\nВыберите действие:",
        "merch_title": "👕 **Каталог одежды Zer0Life**\n\nНаши актуальные релизы:",
    },
    "pl": {
        "welcome": (
            "🚀 **Witaj w Zer0Life Labs AI Hub, {name}!**\n\n"
            "Twoja zdecentralizowana brama do narzędzi AI, rynku P2P, auto-tradingu crypto, "
            "sklepu z odzieżą oraz ekskluzywnych nagród ekosystemu ZRL.\n\n"
            "Wybierz opcję z menu poniżej:"
        ),
        "btn_airdrop": "🎁 ZRL Airdrop i Mini-Gra",
        "btn_farming": "🌾 Farming i Staking ZRL",
        "btn_autotrade": "🤖 Crypto Auto-Trading",
        "btn_p2p": "🤝 Rynek Escrow P2P",
        "btn_merch": "👕 Sklep Zer0Life Merch",
        "btn_sub": "💎 Subskrypcje PRO Crypto",
        "btn_wallet": "🔗 Połącz portfel Web3",
        "btn_stats": "📊 Moje Konto i Statystyki",
        "btn_share": "📢 Udostępnij bota",
        "btn_about": "ℹ️ O Zer0Life Labs",
        "btn_lang": "🌐 Zmień język / Language",
        "farming_title": (
            "🌾 **Pula Farmingu i Stakingu ZRL Zer0Life**\n\n"
            "Zablokuj swoje tokeny ZRL, aby zarabiać wysoki pasywny dochód.\n"
            "• Aktualny APY: `45%`\n"
            "• Staked Balance: `{staked:,.2f} ZRL`\n"
            "• Nagrody do odebrania: `{reward:,.2f} ZRL`\n"
        ),
        "about_text": (
            "ℹ️ **O Zer0Life Labs**\n\n"
            "Zer0Life to niezależna marka streetwearowa oraz projekt Web3 "
            "skupiający się na automatyzacji aktywów kryptograficznych, handlu P2P oraz odzieży.\n\n"
            "• Marka: `zer0life_supply`\n"
            "• Token: `ZRL`\n"
        ),
        "lang_changed": "✅ Język został zmieniony na polski!",
        "p2p_title": "🤝 **Zer0Life P2P Escrow Market**\n\nWybierz opcję:",
        "merch_title": "👕 **Katalog Streetwear Zer0Life**\n\nSprawdź nasze nowości:",
    },
}

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
                wallet_address TEXT DEFAULT NULL,
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_farming (
                user_id INTEGER PRIMARY KEY,
                staked_amount REAL DEFAULT 0.0,
                last_claim_timestamp INTEGER DEFAULT 0,
                reward_rate REAL DEFAULT 0.01
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS merch_catalog (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                price_usd REAL NOT NULL,
                description TEXT,
                stock INTEGER DEFAULT 10
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_system (
                referrer_id INTEGER,
                referred_id INTEGER PRIMARY KEY,
                reward_paid INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    logger.info("SQLite database tables initialized successfully.")

def get_user_lang(user_id: int) -> str:
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute(
            "SELECT language FROM user_usage WHERE telegram_user_id = ?", (user_id,)
        ).fetchone()
        if row and row[0] in TRANSLATIONS:
            return row[0]
    return "en"

def get_text(user_id: int, key: str, **kwargs: Any) -> str:
    lang = get_user_lang(user_id)
    template = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"].get(key, key))
    return template.format(**kwargs)

def register_user_if_not_exists(user_id: int, referrer_id: Optional[int] = None) -> None:
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT telegram_user_id FROM user_usage WHERE telegram_user_id = ?", (user_id,)
        ).fetchone()
        
        if not cursor:
            conn.execute(
                "INSERT INTO user_usage (telegram_user_id, language) VALUES (?, 'en')",
                (user_id,),
            )
            if referrer_id and referrer_id != user_id:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO referral_system (referrer_id, referred_id) VALUES (?, ?)",
                        (referrer_id, user_id)
                    )
                    conn.execute(
                        "UPDATE user_usage SET shared_count = shared_count + 1 WHERE telegram_user_id = ?",
                        (referrer_id,)
                    )
                except Exception as e:
                    logger.error(f"Error recording referral: {e}")
        else:
            conn.execute(
                "UPDATE user_usage SET updated_at = CURRENT_TIMESTAMP WHERE telegram_user_id = ?",
                (user_id,)
            )

def main_menu_keyboard(bot_username: str, user_id: int) -> types.InlineKeyboardMarkup:
    buttons = [
        [types.InlineKeyboardButton(text=get_text(user_id, "btn_airdrop"), callback_data="airdrop:menu")],
        [types.InlineKeyboardButton(text=get_text(user_id, "btn_farming"), callback_data="farming:menu")],
        [types.InlineKeyboardButton(text=get_text(user_id, "btn_autotrade"), callback_data="autotrade:menu")],
        [types.InlineKeyboardButton(text=get_text(user_id, "btn_p2p"), callback_data="p2p:menu")],
        [types.InlineKeyboardButton(text=get_text(user_id, "btn_merch"), callback_data="merch:menu")],
        [types.InlineKeyboardButton(text=get_text(user_id, "btn_sub"), callback_data="sub:choose_currency")],
        [types.InlineKeyboardButton(text=get_text(user_id, "btn_wallet"), callback_data="wallet:connect")],
        [
            types.InlineKeyboardButton(text=get_text(user_id, "btn_stats"), callback_data="stats:view"),
            types.InlineKeyboardButton(text=get_text(user_id, "btn_share"), url=f"https://t.me/share/url?url=https://t.me/{bot_username}?start=ref_{user_id}&text=🚀%20Join%20Zer0Life%20ecosystem!"),
        ],
        [types.InlineKeyboardButton(text=get_text(user_id, "btn_about"), callback_data="info:about")],
        [types.InlineKeyboardButton(text=get_text(user_id, "btn_lang"), callback_data="lang:choose")],
    ]
    if user_id == ADMIN_USER_ID:
        buttons.append([types.InlineKeyboardButton(text="🛠️ Admin Control Panel", callback_data="admin:dashboard")])
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)

class FarmingStakeState(StatesGroup):
    waiting_for_stake_amount = State()

class P2POrderState(StatesGroup):
    waiting_for_pair = State()
    waiting_for_amount = State()
    waiting_for_price = State()
    waiting_for_wallet = State()

class AutoTradeState(StatesGroup):
    waiting_for_pair = State()
    waiting_for_amount = State()
    waiting_for_percent = State()

class AirdropState(StatesGroup):
    waiting_for_solana_address = State()

class SubState(StatesGroup):
    waiting_for_payment_proof = State()

class WalletState(StatesGroup):
    waiting_for_wallet_address = State()

class AdminBroadcastState(StatesGroup):
    waiting_for_broadcast_text = State()

@ROUTER.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    user_id = message.from_user.id
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].replace("ref_", ""))
        except ValueError:
            pass

    register_user_if_not_exists(user_id, referrer_id)
    bot_info = await bot.get_me()
    username = bot_info.username or "Zer0LifeBot"
    name = message.from_user.first_name or "Holder"

    text = get_text(user_id, "welcome", name=name)
    keyboard = main_menu_keyboard(username, user_id)
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "p2p:back_home")
async def back_home_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    bot_info = await bot.get_me()
    username = bot_info.username or "Zer0LifeBot"
    name = callback.from_user.first_name or "Holder"
    text = get_text(user_id, "welcome", name=name)
    keyboard = main_menu_keyboard(username, user_id)
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "info:about")
async def info_about_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    text = get_text(user_id, "about_text")
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")]]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "lang:choose")
async def lang_choose_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:set:en")],
            [types.InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:set:ru")],
            [types.InlineKeyboardButton(text="🇵🇱 Polski", callback_data="lang:set:pl")],
            [types.InlineKeyboardButton(text="⬅️ Back", callback_data="p2p:back_home")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text("🌐 **Select language / Wybierz język:**", parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data.startswith("lang:set:"))
async def lang_set_callback(callback: types.CallbackQuery) -> None:
    lang_code = callback.data.split(":")[-1]
    user_id = callback.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute("UPDATE user_usage SET language = ? WHERE telegram_user_id = ?", (lang_code, user_id))
    await callback.answer(get_text(user_id, "lang_changed"), show_alert=True)
    await back_home_callback(callback)

async def show_farming_menu(user_id: int, event: Any) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute("SELECT staked_amount, last_claim_timestamp FROM user_farming WHERE user_id = ?", (user_id,)).fetchone()
    staked = row[0] if row else 0.0
    last_time = row[1] if row else 0
    reward = 0.0
    if staked > 0 and last_time > 0:
        hours_passed = (now - last_time) / 3600.0
        reward = staked * (0.45 / 8760.0) * hours_passed

    text = get_text(user_id, "farming_title", staked=staked, reward=reward)
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🌾 Stake ZRL", callback_data="farming:stake"), types.InlineKeyboardButton(text="💸 Unstake", callback_data="farming:unstake")],
            [types.InlineKeyboardButton(text="🎁 Claim Rewards", callback_data="farming:claim")],
            [types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")],
        ]
    )
    if isinstance(event, types.CallbackQuery) and event.message:
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    elif isinstance(event, types.Message):
        await event.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "farming:menu")
async def farming_menu_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    await show_farming_menu(callback.from_user.id, callback)

@ROUTER.callback_query(F.data == "farming:stake")
async def farming_stake_callback(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(FarmingStakeState.waiting_for_stake_amount)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="❌ Cancel", callback_data="farming:menu")]])
    if callback.message is not None:
        await callback.message.edit_text("🌾 **Stake ZRL Tokens**\n\nEnter amount:", parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.message(FarmingStakeState.waiting_for_stake_amount)
async def farming_save_stake(message: types.Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0: raise ValueError()
    except ValueError:
        await message.answer("⚠️ Invalid amount.")
        return
    user_id = message.from_user.id
    now = int(datetime.now(timezone.utc).timestamp())
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute("SELECT staked_amount, last_claim_timestamp FROM user_farming WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            conn.execute("INSERT INTO user_farming (user_id, staked_amount, last_claim_timestamp) VALUES (?, ?, ?)", (user_id, amount, now))
        else:
            current_staked, last_time = row
            new_time = last_time if last_time > 0 else now
            conn.execute("UPDATE user_farming SET staked_amount = staked_amount + ?, last_claim_timestamp = ? WHERE user_id = ?", (amount, new_time, user_id))
    await state.clear()
    await message.answer(f"✅ Staked `{amount:,.2f} ZRL`!", parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🌾 Return", callback_data="farming:menu")]]))

@ROUTER.callback_query(F.data == "farming:unstake")
async def farming_unstake_callback(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute("SELECT staked_amount FROM user_farming WHERE user_id = ?", (user_id,)).fetchone()
        staked = row[0] if row else 0.0
        if staked <= 0:
            await callback.answer("⚠️ No active stakes!", show_alert=True)
            return
        conn.execute("UPDATE user_farming SET staked_amount = 0.0, last_claim_timestamp = 0 WHERE user_id = ?", (user_id,))
    await callback.answer(f"Unstaked {staked:,.2f} ZRL!", show_alert=True)
    await show_farming_menu(user_id, callback)

@ROUTER.callback_query(F.data == "farming:claim")
async def farming_claim_callback(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    now = int(datetime.now(timezone.utc).timestamp())
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute("SELECT staked_amount, last_claim_timestamp FROM user_farming WHERE user_id = ?", (user_id,)).fetchone()
        if not row or row[0] <= 0 or row[1] == 0:
            await callback.answer("⚠️ No rewards to claim!", show_alert=True)
            return
        staked, last_time = row
        hours_passed = (now - last_time) / 3600.0
        reward = staked * (0.45 / 8760.0) * hours_passed
        if reward <= 0:
            await callback.answer("⚠️ Too early to claim!", show_alert=True)
            return
        conn.execute("UPDATE user_farming SET last_claim_timestamp = ? WHERE user_id = ?", (now, user_id))
    await callback.answer(f"🎉 Claimed {reward:,.2f} ZRL!", show_alert=True)
    await show_farming_menu(user_id, callback)

async def show_p2p_menu(user_id: int, event: Any) -> None:
    text = get_text(user_id, "p2p_title")
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="📋 Active Listings", callback_data="p2p:list")],
            [types.InlineKeyboardButton(text="➕ Create Order", callback_data="p2p:create")],
            [types.InlineKeyboardButton(text="📦 My Orders", callback_data="p2p:my")],
            [types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")],
        ]
    )
    if isinstance(event, types.CallbackQuery) and event.message:
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    elif isinstance(event, types.Message):
        await event.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "p2p:menu")
async def p2p_menu_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    await show_p2p_menu(callback.from_user.id, callback)

@ROUTER.callback_query(F.data == "p2p:list")
async def p2p_list_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute("SELECT order_id, pair, order_type, amount, price FROM p2p_orders WHERE status = 'active' LIMIT 10").fetchall()
    text = "🤝 **Active P2P Orders:**\n\n" + "".join([f"• **#{o[0]}** | `{o[1]}` | {o[2].upper()} | **{o[3]}** @ `{o[4]}` USD\n" for o in orders]) if orders else "🤝 No active orders."
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Back", callback_data="p2p:menu")]])
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "p2p:create")
async def p2p_create_callback(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(P2POrderState.waiting_for_pair)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="❌ Cancel", callback_data="p2p:menu")]])
    if callback.message is not None:
        await callback.message.edit_text("Enter pair (e.g., `BTC/USD`):", parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.message(P2POrderState.waiting_for_pair)
async def p2p_get_pair(message: types.Message, state: FSMContext) -> None:
    await state.update_data(pair=message.text.strip().upper())
    await state.set_state(P2POrderState.waiting_for_amount)
    await message.answer("Enter amount:")

@ROUTER.message(P2POrderState.waiting_for_amount)
async def p2p_get_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0: raise ValueError()
    except ValueError:
        await message.answer("⚠️ Invalid amount.")
        return
    await state.update_data(amount=amount)
    await state.set_state(P2POrderState.waiting_for_price)
    await message.answer("Enter price in USD:")

@ROUTER.message(P2POrderState.waiting_for_price)
async def p2p_get_price(message: types.Message, state: FSMContext) -> None:
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0: raise ValueError()
    except ValueError:
        await message.answer("⚠️ Invalid price.")
        return
    await state.update_data(price=price)
    await state.set_state(P2POrderState.waiting_for_wallet)
    await message.answer("Enter your crypto payout wallet:")

@ROUTER.message(P2POrderState.waiting_for_wallet)
async def p2p_get_wallet(message: types.Message, state: FSMContext) -> None:
    wallet = message.text.strip()
    data = await state.get_data()
    user_id = message.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        cursor = conn.execute("INSERT INTO p2p_orders (seller_id, pair, order_type, amount, price, seller_wallet, status) VALUES (?, ?, 'sell', ?, ?, ?, 'active')", (user_id, data["pair"], data["amount"], data["price"], wallet))
        order_id = cursor.lastrowid
    await state.clear()
    await message.answer(f"✅ P2P Order #{order_id} created!", parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="P2P Menu", callback_data="p2p:menu")]]))

@ROUTER.callback_query(F.data == "p2p:my")
async def p2p_my_orders(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        orders = conn.execute("SELECT order_id, pair, order_type, amount, price, status FROM p2p_orders WHERE seller_id = ?", (user_id,)).fetchall()
    text = "📦 **My P2P Orders:**\n\n" + "".join([f"• **#{o[0]}** | `{o[1]}` | {o[3]} @ {o[4]} USD | `{o[5]}`\n" for o in orders]) if orders else "📦 No orders found."
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Back", callback_data="p2p:menu")]])
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "autotrade:menu")
async def autotrade_menu_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute("SELECT pair, amount, target_percent, status FROM auto_trade WHERE user_id = ?", (user_id,)).fetchone()
    status_text = "🔴 Inactive" if not row or row[3] == 'inactive' else f"🟢 Active ({row[3].upper()})"
    details = "No active trading configuration." if not row or row[3] == 'inactive' else f"Pair: `{row[0]}` | Amount: `{row[1]}` | Target: `+{row[2]}%`"
    text = f"🤖 **Crypto Auto-Trading Bot**\n\nStatus: {status_text}\n{details}"
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="⚙️ Configure Bot", callback_data="autotrade:config")],
            [types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "autotrade:config")
async def autotrade_config_callback(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AutoTradeState.waiting_for_pair)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="❌ Cancel", callback_data="autotrade:menu")]])
    if callback.message is not None:
        await callback.message.edit_text("🤖 Enter pair (e.g., `BTC/USDT`):", parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.message(AutoTradeState.waiting_for_pair)
async def autotrade_get_pair(message: types.Message, state: FSMContext) -> None:
    await state.update_data(pair=message.text.strip().upper())
    await state.set_state(AutoTradeState.waiting_for_amount)
    await message.answer("Enter trade amount in USDT:")

@ROUTER.message(AutoTradeState.waiting_for_amount)
async def autotrade_get_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0: raise ValueError()
    except ValueError:
        await message.answer("⚠️ Invalid amount.")
        return
    await state.update_data(amount=amount)
    await state.set_state(AutoTradeState.waiting_for_percent)
    await message.answer("Enter target take-profit % (e.g., `5.5`):")

@ROUTER.message(AutoTradeState.waiting_for_percent)
async def autotrade_get_percent(message: types.Message, state: FSMContext) -> None:
    try:
        percent = float(message.text.strip().replace(",", "."))
        if percent <= 0: raise ValueError()
    except ValueError:
        await message.answer("⚠️ Invalid percent.")
        return
    data = await state.get_data()
    user_id = message.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute("INSERT INTO auto_trade (user_id, pair, amount, target_percent, status) VALUES (?, ?, ?, ?, 'active') ON CONFLICT(user_id) DO UPDATE SET pair=excluded.pair, amount=excluded.amount, target_percent=excluded.target_percent, status='active'", (user_id, data["pair"], data["amount"], percent))
    await state.clear()
    await message.answer(f"✅ Auto-Trading Activated for `{data['pair']}`!", parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="Menu", callback_data="autotrade:menu")]]))

@ROUTER.callback_query(F.data == "airdrop:menu")
async def airdrop_menu_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute("SELECT reward_amount, status FROM airdrop_claims WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        text = "🎁 **ZRL Airdrop**\n\nYou are eligible for an exclusive drop of `500 ZRL`!"
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🎯 Claim Airdrop", callback_data="airdrop:claim_start")], [types.InlineKeyboardButton(text="⬅️ Menu", callback_data="p2p:back_home")]])
    else:
        text = f"🎁 **ZRL Airdrop Status**\n\nClaim submitted for `{row[0]} ZRL`. Status: `{row[1].upper()}`"
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Menu", callback_data="p2p:back_home")]])
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "airdrop:claim_start")
async def airdrop_claim_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AirdropState.waiting_for_solana_address)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="❌ Cancel", callback_data="airdrop:menu")]])
    if callback.message is not None:
        await callback.message.edit_text("🎯 Enter your Solana wallet address:", parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.message(AirdropState.waiting_for_solana_address)
async def airdrop_save_address(message: types.Message, state: FSMContext) -> None:
    address = message.text.strip()
    if len(address) < 32:
        await message.answer("⚠️ Invalid Solana address.")
        return
    user_id = message.from_user.id
    reward = 500.0
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute("INSERT INTO airdrop_claims (user_id, reward_amount, solana_address, status) VALUES (?, ?, ?, 'pending') ON CONFLICT(user_id) DO UPDATE SET solana_address=excluded.solana_address", (user_id, reward, address))
    await state.clear()
    await message.answer(f"✅ Airdrop Claimed for `{address}`!", parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="Menu", callback_data="airdrop:menu")]]))

@ROUTER.callback_query(F.data == "wallet:connect")
async def wallet_connect_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute("SELECT wallet_address FROM user_usage WHERE telegram_user_id = ?", (user_id,)).fetchone()
    wallet = row[0] if row and row[0] else "Not Connected"
    text = f"🔗 **Web3 Wallet Connector**\n\nLinked Wallet:\n`{wallet}`"
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🔗 Connect / Update", callback_data="wallet:input")],
            [types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "wallet:input")
async def wallet_input_callback(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(WalletState.waiting_for_wallet_address)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="❌ Cancel", callback_data="wallet:connect")]])
    if callback.message is not None:
        await callback.message.edit_text("🔗 Send your EVM / Solana wallet address:", parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.message(WalletState.waiting_for_wallet_address)
async def wallet_save_address(message: types.Message, state: FSMContext) -> None:
    address = message.text.strip()
    if len(address) < 10:
        await message.answer("⚠️ Invalid address.")
        return
    user_id = message.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute("UPDATE user_usage SET wallet_address = ? WHERE telegram_user_id = ?", (address, user_id))
    await state.clear()
    await message.answer(f"✅ Wallet connected: `{address}`", parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="Wallet Menu", callback_data="wallet:connect")]]))

@ROUTER.callback_query(F.data == "merch:menu")
async def merch_menu_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    text = get_text(user_id, "merch_title") + "\n\n• **Zer0Life Heavyweight Hoodie** — `$85.00`\n• **Zer0Life Cyberpunk Tee** — `$45.00`"
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🛒 Order via Bot", callback_data="merch:order")],
            [types.InlineKeyboardButton(text="🌐 Instagram", url="https://instagram.com/zer0life_supply")],
            [types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "merch:order")
async def merch_order_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    text = "👕 Contact manager at `@zer0life_supply` for custom sizing."
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Back", callback_data="merch:menu")]])
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "sub:choose_currency")
async def sub_choose_currency(callback: types.CallbackQuery) -> None:
    await callback.answer()
    text = "💎 **PRO Subscription** ($29/mo)\n\nSelect payment currency:"
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="⚡ USDT", callback_data="sub_pay:USDT"), types.InlineKeyboardButton(text="₿ Bitcoin", callback_data="sub_pay:BTC")],
            [types.InlineKeyboardButton(text="🪙 ZRL", callback_data="sub_pay:ZRL")],
            [types.InlineKeyboardButton(text="⬅️ Menu", callback_data="p2p:back_home")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data.startswith("sub_pay:"))
async def sub_pay_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    currency = callback.data.split(":")[-1]
    await state.update_data(sub_currency=currency)
    await state.set_state(SubState.waiting_for_payment_proof)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="❌ Cancel", callback_data="sub:choose_currency")]])
    if callback.message is not None:
        await callback.message.edit_text(f"💎 Send payment in **{currency}** and send TXID here:", parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.message(SubState.waiting_for_payment_proof)
async def sub_receive_proof(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        conn.execute("UPDATE user_usage SET is_pro = 1 WHERE telegram_user_id = ?", (user_id,))
    await state.clear()
    await message.answer("🎉 **PRO Activated Successfully!**", parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="Menu", callback_data="p2p:back_home")]]))

@ROUTER.callback_query(F.data == "stats:view")
async def stats_view_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        row = conn.execute("SELECT generations_used, is_pro, shared_count, language, updated_at FROM user_usage WHERE telegram_user_id = ?", (user_id,)).fetchone()
    generations, is_pro, shared, lang, updated = row if row else (0, 0, 0, "en", "N/A")
    pro_status = "💎 PRO Active" if is_pro else "🛡️ Standard Tier"
    
    # Расчет статуса активности (онлайн / офлайн по последнему времени обновления)
    text = (
        f"📊 **User Account & Statistics**\n\n"
        f"• Telegram ID: `{user_id}`\n"
        f"• Status: {pro_status}\n"
        f"• Referrals Shared: `{shared}`\n"
        f"• Language: `{lang.upper()}`\n"
        f"• Last Activity: `{updated}`\n"
        f"• Session State: 🟢 Online (Active in Session)"
    )
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")]])
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "admin:dashboard")
async def admin_dashboard_callback(callback: types.CallbackQuery) -> None:
    if callback.from_user.id != ADMIN_USER_ID:
        await callback.answer("⚠️ Access denied.", show_alert=True)
        return
    await callback.answer()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM user_usage").fetchone()[0]
        total_orders = conn.execute("SELECT COUNT(*) FROM p2p_orders").fetchone()[0]
        total_staked = conn.execute("SELECT SUM(staked_amount) FROM user_farming").fetchone()[0] or 0.0
    text = (
        f"🛠️ **Admin Control Panel**\n\n"
        f"• Total Users: `{total_users}`\n"
        f"• P2P Orders: `{total_orders}`\n"
        f"• ZRL Staked: `{total_staked:,.2f} ZRL`\n"
        f"• System Status: 🟢 Operational"
    )
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="📢 Broadcast Message", callback_data="admin:broadcast")],
            [types.InlineKeyboardButton(text="⬅️ Main Menu", callback_data="p2p:back_home")],
        ]
    )
    if callback.message is not None:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_callback(callback: types.CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != ADMIN_USER_ID: return
    await callback.answer()
    await state.set_state(AdminBroadcastState.waiting_for_broadcast_text)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="❌ Cancel", callback_data="admin:dashboard")]])
    if callback.message is not None:
        await callback.message.edit_text("📢 Enter broadcast text:", parse_mode="Markdown", reply_markup=keyboard)

@ROUTER.message(AdminBroadcastState.waiting_for_broadcast_text)
async def admin_send_broadcast(message: types.Message, state: FSMContext) -> None:
    if message.from_user.id != ADMIN_USER_ID: return
    text = message.text
    await state.clear()
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        users = conn.execute("SELECT telegram_user_id FROM user_usage").fetchall()
    success, failed = 0, 0
    for u in users:
        try:
            await bot.send_message(u[0], f"📢 **Announcement:**\n\n{text}", parse_mode="Markdown")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await message.answer(f"✅ Broadcast finished!\nDelivered: `{success}` | Failed: `{failed}`", parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="Admin Panel", callback_data="admin:dashboard")]]))

async def main() -> None:
    init_db()
    logger.info("Starting Zer0Life Labs AI Bot polling service...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped gracefully.")
