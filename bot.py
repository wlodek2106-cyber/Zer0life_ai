"""Zer0Life Commerce AI Telegram bot.

The bot receives a product photo and generates an English/Polish marketplace
listing with OpenAI's vision-capable model.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import io
import logging
import os
import secrets
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Final

import aiohttp
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from openai import AsyncOpenAI


LOGGER = logging.getLogger(__name__)
ROUTER = Router()
INVOICE_MONITOR_TASKS: set[asyncio.Task[None]] = set()
BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def utc_now() -> datetime:
    """Return a naive UTC datetime for the existing SQLite ISO format."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


MAX_TELEGRAM_MESSAGE_LENGTH: Final[int] = 4096
FREE_GENERATIONS_PER_USER: Final[int] = 3
PRO_SUBSCRIPTION_DAYS: Final[int] = 30
ZRL_RENEWAL_COST: Final[float] = 100_000.0
BOT_USERNAME: Final[str] = "ZEROLIFEAiCOMMERCE_bot"
USAGE_DB_PATH: Final[str] = os.getenv(
    "USAGE_DB_PATH",
    str(Path(__file__).resolve().parent / "usage.sqlite3"),
)
OPENAI_MODEL: Final[str] = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_IMAGE_MODEL: Final[str] = os.getenv(
    "OPENAI_IMAGE_MODEL",
    "gpt-image-1",
)
OPENAI_VISION_TIMEOUT_SECONDS: Final[int] = 60
OPENAI_IMAGE_TIMEOUT_SECONDS: Final[int] = 120
CRYPTOBOT_API_BASE: Final[str] = "https://pay.crypt.bot/api/"
CRYPTO_INVOICE_AMOUNT: Final[str] = "10"
CRYPTO_DONATION_AMOUNT: Final[str] = "10"
CRYPTO_INVOICE_TTL_SECONDS: Final[int] = 3600
CRYPTO_POLL_INTERVAL_SECONDS: Final[int] = 10
DEPOSIT_SOLANA_WALLET: Final[str] = os.getenv(
    "DEPOSIT_SOLANA_WALLET",
    "BEUYL2iHN1PPsMvE94pMT3aKbJAVsF4HqBgzwYSu2Wn7",
)
ZRL_MINT_ADDRESS: Final[str] = os.getenv(
    "ZRL_MINT_ADDRESS",
    "AyfWjjJ9FPYfsgKCUaZ7PwMpoHi8ujf4m3pYxy7MmDiq",
)
PRO_SUBSCRIPTION_USD_PRICE: Final[Decimal] = Decimal(
    os.getenv("PRO_SUBSCRIPTION_USD_PRICE", "10.0")
)
JUPITER_PRICE_V2_URL: Final[str] = "https://api.jup.ag/price/v2"
JUPITER_PRICE_V3_URL: Final[str] = "https://api.jup.ag/price/v3"
SOLANA_RPC_URL: Final[str] = os.getenv(
    "SOLANA_RPC_URL",
    "https://api.mainnet-beta.solana.com",
)
SOLANA_EXPLORER_URL: Final[str] = "https://solscan.io/tx/"
ZRL_API_TIMEOUT_SECONDS: Final[int] = 20
ZRL_INVOICE_TTL_SECONDS: Final[int] = 15 * 60
ZRL_UNIQUE_AMOUNT_VARIANTS: Final[int] = 100_000
REVOLUT_PAYMENT_URL: Final[str] = "https://revolut.me/vpalamarchuk91"
PAYPAL_PAYMENT_URL: Final[str] = "https://www.paypal.me/Volodymyr222/10usd"

PAYMENT_METHODS: Final[dict[str, str]] = {
    "card": "💳 Bank Card / BLIK",
    "crypto": "🪙 CryptoBot (USDT)",
    "zrl": "💎 Оплатить токеном ZRL (Solana)",
    "paypal": "🅿️ PayPal / International",
}

LISTING_PROMPT: Final[str] = """
You are an e-commerce copywriter specializing in Etsy, Shopify, and Allegro.
Analyze the product photo carefully. Do not invent brand names, materials,
measurements, certifications, or features that cannot be reasonably inferred
from the image. If an important detail is uncertain, use neutral wording.

Create a complete marketplace listing in English and Polish.

Use exactly this structure:

📦 PRODUCT LISTING

📌 Title (EN): [SEO-friendly title, maximum 140 characters]
📌 Title (PL): [Natural Polish title]

📝 Description (EN):
[Clear, persuasive description. Mention only visible or safely inferable details.]

📝 Description (PL):
[Natural Polish translation/adaptation of the description.]

🏷️ SEO Tags (13):
[Exactly 13 unique, relevant tags. Every tag must be 20 characters or
fewer, including spaces. Do not use # symbols. Use short searchable phrases.]

📋 Copy-paste tags:
[The same 13 tags on one line, formatted exactly as:
tag1, tag2, tag3, tag4, tag5, tag6, tag7, tag8, tag9, tag10, tag11, tag12, tag13]
""".strip()


def get_required_env(name: str) -> str:
    """Read a required environment variable with a useful startup error."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Add it to environment variables before starting the bot."
        )
    return value


def init_usage_db() -> None:
    """Create local usage and payment tables if they do not exist yet."""
    with sqlite3.connect(USAGE_DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_usage (
                telegram_user_id INTEGER PRIMARY KEY,
                generations_used INTEGER NOT NULL DEFAULT 0
                    CHECK (generations_used >= 0),
                subscription_status TEXT NOT NULL DEFAULT 'free',
                subscription_plan TEXT NOT NULL DEFAULT 'free',
                is_pro INTEGER NOT NULL DEFAULT 0,
                paid_at TEXT,
                crypto_invoice_id INTEGER,
                referrer_id INTEGER,
                referral_count INTEGER NOT NULL DEFAULT 0,
                bonus_generations INTEGER NOT NULL DEFAULT 0,
                subscription_expiry TEXT,
                auto_renew INTEGER NOT NULL DEFAULT 1,
                notified_expiry INTEGER NOT NULL DEFAULT 0,
                zrl_balance REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS crypto_invoices (
                invoice_id INTEGER PRIMARY KEY,
                telegram_user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                paid_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_photos (
                photo_token TEXT PRIMARY KEY,
                telegram_user_id INTEGER NOT NULL,
                telegram_file_id TEXT NOT NULL,
                caption TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def register_user(
    telegram_user_id: int,
    referrer_id: int | None = None,
) -> bool:
    """Create a user and atomically reward a valid first-time referral."""
    connection = sqlite3.connect(
        USAGE_DB_PATH,
        timeout=30,
        isolation_level="IMMEDIATE",
    )
    try:
        existing_user = connection.execute(
            """
            SELECT 1
            FROM user_usage
            WHERE telegram_user_id = ?
            """,
            (telegram_user_id,),
        ).fetchone()

        if existing_user is not None:
            connection.commit()
            return False

        valid_referrer_id = (
            referrer_id
            if referrer_id is not None
            and referrer_id > 0
            and referrer_id != telegram_user_id
            else None
        )

        connection.execute(
            """
            INSERT INTO user_usage
                (telegram_user_id, generations_used, referrer_id)
            VALUES (?, 0, ?)
            """,
            (telegram_user_id, valid_referrer_id),
        )

        if valid_referrer_id is None:
            connection.commit()
            return False

        connection.execute(
            """
            INSERT OR IGNORE INTO user_usage
                (telegram_user_id, generations_used)
            VALUES (?, 0)
            """,
            (valid_referrer_id,),
        )

        connection.execute(
            """
            UPDATE user_usage
            SET referral_count = referral_count + 1,
                bonus_generations = bonus_generations + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_user_id = ?
            """,
            (valid_referrer_id,),
        )

        connection.commit()
        return True

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_referral_stats(
    telegram_user_id: int,
) -> tuple[int, int]:
    """Return invited-user count and available bonus generations."""
    with sqlite3.connect(USAGE_DB_PATH) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO user_usage
                (telegram_user_id, generations_used)
            VALUES (?, 0)
            """,
            (telegram_user_id,),
        )

        row = connection.execute(
            """
            SELECT referral_count, bonus_generations
            FROM user_usage
            WHERE telegram_user_id = ?
            """,
            (telegram_user_id,),
        ).fetchone()

    return int(row[0]), int(row[1])


def save_pending_photo(
    telegram_user_id: int,
    telegram_file_id: str,
    caption: str | None,
) -> str:
    """Store a Telegram photo reference for subsequent actions."""
    photo_token = secrets.token_hex(8)

    with sqlite3.connect(USAGE_DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO pending_photos
                (photo_token, telegram_user_id, telegram_file_id, caption)
            VALUES (?, ?, ?, ?)
            """,
            (
                photo_token,
                telegram_user_id,
                telegram_file_id,
                caption[:2000] if caption else None,
            ),
        )

    return photo_token


def get_pending_photo(
    photo_token: str,
    telegram_user_id: int,
) -> tuple[str, str | None] | None:
    """Load a saved photo only for its owner."""
    with sqlite3.connect(USAGE_DB_PATH) as connection:
        row = connection.execute(
            """
            SELECT telegram_file_id, caption
            FROM pending_photos
            WHERE photo_token = ?
              AND telegram_user_id = ?
            """,
            (photo_token, telegram_user_id),
        ).fetchone()

    if row is None:
        return None

    return (str(row[0]), str(row[1]) if row[1] is not None else None)


def reserve_free_generation(
    telegram_user_id: int,
) -> str | None:
    """Atomically consume a standard or bonus generation."""
    connection = sqlite3.connect(
        USAGE_DB_PATH,
        timeout=30,
        isolation_level="IMMEDIATE",
    )
    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO user_usage
                (telegram_user_id, generations_used)
            VALUES (?, 0)
            """,
            (telegram_user_id,),
        )

        updated = connection.execute(
            """
            UPDATE user_usage
            SET generations_used = generations_used + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_user_id = ?
              AND generations_used < ?
            """,
            (telegram_user_id, FREE_GENERATIONS_PER_USER),
        )

        if updated.rowcount == 1:
            connection.commit()
            return "standard"

        updated = connection.execute(
            """
            UPDATE user_usage
            SET bonus_generations = bonus_generations - 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_user_id = ?
              AND bonus_generations > 0
            """,
            (telegram_user_id,),
        )

        connection.commit()
        return "bonus" if updated.rowcount == 1 else None

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def refund_free_generation(
    telegram_user_id: int,
    source: str,
) -> None:
    """Return a reserved generation when processing fails."""
    with sqlite3.connect(USAGE_DB_PATH) as connection:
        if source == "bonus":
            connection.execute(
                """
                UPDATE user_usage
                SET bonus_generations = bonus_generations + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            )
        elif source == "standard":
            connection.execute(
                """
                UPDATE user_usage
                SET generations_used = CASE
                        WHEN generations_used > 0
                        THEN generations_used - 1
                        ELSE 0
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            )


def is_user_paid(telegram_user_id: int) -> bool:
    """Return whether the user has active unlimited access."""
    with sqlite3.connect(USAGE_DB_PATH) as connection:
        row = connection.execute(
            """
            SELECT subscription_status,
                   subscription_plan,
                   is_pro,
                   subscription_expiry
            FROM user_usage
            WHERE telegram_user_id = ?
            """,
            (telegram_user_id,),
        ).fetchone()

    if not row:
        return False

    if not (row[0] == "paid" or row[1] == "unlimited" or bool(row[2])):
        return False

    if not row[3]:
        return True

    try:
        return datetime.fromisoformat(str(row[3])) > utc_now()
    except ValueError:
        return False


def main_menu_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="💎 Купить Pro / Безлимит",
                    callback_data="sub:menu",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="🤝 Партнёрская программа",
                    callback_data="referral:menu",
                )
            ],
        ]
    )


def photo_action_keyboard(photo_token: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="📦 SEO-карточка (EN/PL)",
                    callback_data=f"photo:seo:{photo_token}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="✂️ Удалить фон",
                    callback_data=f"photo:remove:{photo_token}",
                ),
                types.InlineKeyboardButton(
                    text="🌄 Заменить фон",
                    callback_data=f"photo:background:{photo_token}",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="🧍 Примерка на модель",
                    callback_data=f"photo:model:{photo_token}",
                )
            ],
        ]
    )


def subscription_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=PAYMENT_METHODS["crypto"],
                    callback_data="pay:crypto",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=PAYMENT_METHODS["card"],
                    callback_data="pay:card",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=PAYMENT_METHODS["paypal"],
                    callback_data="pay:paypal",
                )
            ],
        ]
    )


@ROUTER.message(Command("start"))
async def start_handler(message: types.Message) -> None:
    telegram_user_id = message.from_user.id
    referrer_id: int | None = None

    command_parts = (message.text or "").split(maxsplit=1)
    if len(command_parts) == 2 and command_parts[1].startswith("ref_"):
        try:
            referrer_id = int(command_parts[1].removeprefix("ref_"))
        except ValueError:
            pass

    referral_rewarded = await asyncio.to_thread(
        register_user,
        telegram_user_id,
        referrer_id,
    )

    if referral_rewarded and referrer_id is not None:
        try:
            await message.bot.send_message(
                referrer_id,
                "🎉 Новый реферал зарегистрировался! Вам начислена 1 бесплатная генерация.",
                parse_mode=None,
            )
        except Exception:
            pass

    await message.answer(
        "👋 Я <b>Zer0Life Commerce AI</b>.\n\n"
        "📸 Отправь фото товара, и я создам:\n"
        "• SEO-заголовок и описание на EN и PL\n"
        "• 13 SEO-тегов\n"
        "• Удаление/замену фона и примерку на модель\n\n"
        "Команда /help покажет подсказку.",
        reply_markup=main_menu_keyboard(),
    )


@ROUTER.message(Command("ref"))
async def referral_command_handler(message: types.Message) -> None:
    await send_referral_menu(message, message.from_user.id)


async def send_referral_menu(
    message: types.Message,
    telegram_user_id: int,
) -> None:
    referral_count, bonus_generations = await asyncio.to_thread(
        get_referral_stats,
        telegram_user_id,
    )
    referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{telegram_user_id}"

    await message.answer(
        "🤝 Партнёрская программа\n\n"
        f"Ваша уникальная ссылка:\n{referral_link}\n\n"
        f"👥 Приглашено пользователей: {referral_count}\n"
        f"🎁 Доступно бонусных генераций: {bonus_generations}",
        parse_mode=None,
    )


@ROUTER.callback_query(F.data == "referral:menu")
async def referral_menu_handler(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await send_referral_menu(callback.message, callback.from_user.id)


@ROUTER.callback_query(F.data == "sub:menu")
async def sub_menu_handler(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "💎 <b>Pro-доступ (30 дней)</b> — 10 USD\n\n"
            "Выберите удобный способ оплаты:",
            reply_markup=subscription_keyboard(),
        )


@ROUTER.callback_query(F.data == "pay:card")
async def pay_card_handler(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"💳 Оплата картой / BLIK:\n\nПерейдите по ссылке для оплаты: {REVOLUT_PAYMENT_URL}\n\n"
            "После оплаты отправьте квитанцию или напишите администратору.",
            parse_mode=None,
        )


@ROUTER.callback_query(F.data == "pay:paypal")
async def pay_paypal_handler(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"🅿️ Оплата через PayPal:\n\nСсылка: {PAYPAL_PAYMENT_URL}\n\n"
            "После оплаты сообщите администратору для активации Pro.",
            parse_mode=None,
        )


@ROUTER.callback_query(F.data == "pay:crypto")
async def pay_crypto_handler(callback: types.CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    api_token = os.getenv("CRYPTOBOT_API_TOKEN", "").strip()
    if not api_token:
        await callback.message.answer(
            "⚠️ Криптоплатежи временно недоступны (не настроен токен)."
        )
        return
    
    try:
        async with aiohttp.ClientSession(
            base_url=CRYPTOBOT_API_BASE,
            headers={"Crypto-Pay-API-Token": api_token}
        ) as session:
            async with session.post("createInvoice", json={
                "asset": "USDT",
                "amount": "10",
                "description": "Zer0Life Commerce AI Pro 30 days",
                "payload": f"zerolife:{callback.from_user.id}",
                "expires_in": 3600
            }) as resp:
                data = await resp.json()
                if data.get("ok"):
                    url = data["result"].get("bot_invoice_url") or data["result"].get("pay_url")
                    await callback.message.answer(
                        f"🪙 Счёт создан через CryptoBot:\n\nОплатите по ссылке: {url}",
                        parse_mode=None
                    )
                else:
                    await callback.message.answer("⚠️ Не удалось создать криптосчёт.")
    except Exception:
        await callback.message.answer("⚠️ Ошибка связи с платежным шлюзом.")


@ROUTER.message(Command("help"))
async def help_handler(message: types.Message) -> None:
    await message.answer(
        "Отправь фото товара одним сообщением и выбери действие: создание SEO-карточки, удаление или замена фона, примерка на модель."
    )


@ROUTER.message(F.photo)
async def photo_upload_handler(message: types.Message) -> None:
    photo_token = await asyncio.to_thread(
        save_pending_photo,
        message.from_user.id,
        message.photo[-1].file_id,
        message.caption,
    )
    await message.answer(
        "Выберите, что сделать с фотографией:",
        reply_markup=photo_action_keyboard(photo_token),
    )


@ROUTER.callback_query(F.data.startswith("photo:"))
async def photo_action_handler(
    callback: types.CallbackQuery,
    bot: Bot,
    openai_client: AsyncOpenAI,
) -> None:
    parts = (callback.data or "").split(":", maxsplit=2)
    if len(parts) != 3:
        await callback.answer("Некорректное действие.", show_alert=True)
        return

    _, action, photo_token = parts
    pending_photo = await asyncio.to_thread(
        get_pending_photo,
        photo_token,
        callback.from_user.id,
    )

    if pending_photo is None:
        await callback.answer("Фото не найдено. Отправьте снова.", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    telegram_file_id, caption = pending_photo
    await callback.answer()

    if action == "seo":
        has_unlimited = is_user_paid(callback.from_user.id)
        source = "unlimited" if has_unlimited else reserve_free_generation(callback.from_user.id)
        
        if not source:
            await callback.message.answer(
                "⛔ Бесплатный лимит исчерпан. Выберите способ оплаты:",
                reply_markup=subscription_keyboard(),
            )
            return

        status_msg = await callback.message.answer("⏳ Генерирую SEO-карточку...")
        try:
            file_info = await bot.get_file(telegram_file_id)
            img_stream = io.BytesIO()
            await bot.download_file(file_info.file_path, destination=img_stream)
            b64_img = base64.b64encode(img_stream.getvalue()).decode("ascii")

            response = await openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": LISTING_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                    ]
                }],
                max_tokens=1200
            )
            result_text = response.choices[0].message.content
            await status_msg.delete()
            await callback.message.answer(result_text, parse_mode=None)
        except Exception:
            if source in {"standard", "bonus"}:
                refund_free_generation(callback.from_user.id, source)
            await status_msg.edit_text("⚠️ Ошибка генерации. Попробуйте еще раз.")
        return


@ROUTER.message()
async def unsupported_message_handler(message: types.Message) -> None:
    await message.answer("Пожалуйста, отправь фото товара. Для начала работы используй /start.")


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

        telegram_token = "8820567588:AAHmA_oj9AyKVqjAFWEoo-ecjHOvhYi6fHg"
    openai_api_key = get_required_env("OPENAI_API_KEY")

    bot = Bot(token=telegram_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    openai_client = AsyncOpenAI(api_key=openai_api_key)

    init_usage_db()
    dispatcher = Dispatcher()
    dispatcher.include_router(ROUTER)

    LOGGER.info("Zer0Life Commerce AI is starting...")
    
    # Жестко сбрасываем старые зависшие сессии Telegram перед стартом
    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot, openai_client=openai_client)

        
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(1) from exc
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("Bot stopped.")


