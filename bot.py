"""Zer0Life Commerce AI Telegram bot.

The bot receives a product photo and generates an English/Polish marketplace
listing with OpenAI's vision-capable model.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import os
import secrets
import sqlite3
import time
from decimal import Decimal, InvalidOperation, ROUND_UP
from pathlib import Path
from typing import Final

import aiohttp
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from openai import AsyncOpenAI
from keep_alive import keep_alive
import etsy


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
REVOLUT_PAYMENT_URL: Final[str] = (
    "https://revolut.me/vpalamarchuk91"
)
PAYPAL_PAYMENT_URL: Final[str] = "https://www.paypal.me/Volodymyr222/10usd"
PAYMENT_METHODS: Final[dict[str, str]] = {
    "card": "💳 Bank Card / BLIK",
    "crypto": "🪙 CryptoBot (USDT)",
    "zrl": "💎 Оплатить токеном ZRL (Solana)",
    "paypal": "🅿️ PayPal / International",
}
TRANSLATION_LANGUAGES: Final[dict[str, str]] = {
    "en": "English",
    "pl": "Polish",
    "de": "German",
    "ua": "Ukrainian",
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

🏷️ Etsy SEO Tags (13):
[Exactly 13 unique, relevant Etsy tags. Every tag must be 20 characters or
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
            "Add it to Replit Secrets before starting the bot."
        )
    return value


def init_usage_db() -> None:
    """Create the local usage table if it does not exist yet."""

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
        existing_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(user_usage)")
        }
        migrations = {
            "subscription_status": (
                "ALTER TABLE user_usage ADD COLUMN "
                "subscription_status TEXT NOT NULL DEFAULT 'free'"
            ),
            "subscription_plan": (
                "ALTER TABLE user_usage ADD COLUMN "
                "subscription_plan TEXT NOT NULL DEFAULT 'free'"
            ),
            "is_pro": (
                "ALTER TABLE user_usage ADD COLUMN "
                "is_pro INTEGER NOT NULL DEFAULT 0"
            ),
            "paid_at": "ALTER TABLE user_usage ADD COLUMN paid_at TEXT",
            "crypto_invoice_id": (
                "ALTER TABLE user_usage ADD COLUMN crypto_invoice_id INTEGER"
            ),
            "referrer_id": (
                "ALTER TABLE user_usage ADD COLUMN referrer_id INTEGER"
            ),
            "referral_count": (
                "ALTER TABLE user_usage ADD COLUMN "
                "referral_count INTEGER NOT NULL DEFAULT 0"
            ),
            "bonus_generations": (
                "ALTER TABLE user_usage ADD COLUMN "
                "bonus_generations INTEGER NOT NULL DEFAULT 0"
            ),
            "subscription_expiry": (
                "ALTER TABLE user_usage ADD COLUMN subscription_expiry TEXT"
            ),
            "auto_renew": (
                "ALTER TABLE user_usage ADD COLUMN "
                "auto_renew INTEGER NOT NULL DEFAULT 1"
            ),
            "notified_expiry": (
                "ALTER TABLE user_usage ADD COLUMN "
                "notified_expiry INTEGER NOT NULL DEFAULT 0"
            ),
            "zrl_balance": (
                "ALTER TABLE user_usage ADD COLUMN "
                "zrl_balance REAL NOT NULL DEFAULT 0.0"
            ),
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)

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
            CREATE TABLE IF NOT EXISTS generated_listings (
                listing_token TEXT PRIMARY KEY,
                telegram_user_id INTEGER NOT NULL,
                listing_text TEXT NOT NULL,
                telegram_file_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        generated_listing_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(generated_listings)"
            )
        }
        if "telegram_file_id" not in generated_listing_columns:
            connection.execute(
                "ALTER TABLE generated_listings ADD COLUMN telegram_file_id TEXT"
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS etsy_oauth_states (
                state TEXT PRIMARY KEY,
                telegram_user_id INTEGER NOT NULL,
                listing_token TEXT NOT NULL,
                code_verifier TEXT NOT NULL,
                created_at_epoch INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS etsy_connections (
                telegram_user_id INTEGER PRIMARY KEY,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at_epoch INTEGER NOT NULL,
                etsy_user_id INTEGER NOT NULL,
                shop_id INTEGER,
                shop_name TEXT,
                available_shops TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS etsy_exports (
                telegram_user_id INTEGER PRIMARY KEY,
                listing_token TEXT NOT NULL,
                status TEXT NOT NULL,
                shop_id INTEGER,
                title TEXT,
                price TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS zrl_invoices (
                invoice_token TEXT PRIMARY KEY,
                telegram_user_id INTEGER NOT NULL,
                usd_price TEXT NOT NULL,
                zrl_price_usd TEXT NOT NULL,
                required_zrl TEXT NOT NULL,
                required_raw_amount TEXT NOT NULL,
                token_decimals INTEGER NOT NULL,
                price_source TEXT NOT NULL,
                tx_signature TEXT,
                status TEXT NOT NULL DEFAULT 'awaiting_tx',
                verification_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at_epoch INTEGER NOT NULL,
                expires_at_epoch INTEGER NOT NULL,
                reviewed_at TEXT,
                paid_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS consumed_zrl_signatures (
                tx_signature TEXT PRIMARY KEY,
                invoice_token TEXT NOT NULL UNIQUE,
                telegram_user_id INTEGER NOT NULL,
                consumed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        zrl_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(zrl_invoices)")
        }
        if "created_at_epoch" not in zrl_columns:
            connection.execute(
                "ALTER TABLE zrl_invoices "
                "ADD COLUMN created_at_epoch INTEGER NOT NULL DEFAULT 0"
            )
        if "expires_at_epoch" not in zrl_columns:
            connection.execute(
                "ALTER TABLE zrl_invoices "
                "ADD COLUMN expires_at_epoch INTEGER NOT NULL DEFAULT 0"
            )
        zrl_table_sql_row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'zrl_invoices'
            """
        ).fetchone()
        zrl_table_sql = str(zrl_table_sql_row[0] if zrl_table_sql_row else "")
        if "TX_SIGNATURE TEXT UNIQUE" in zrl_table_sql.upper():
            LOGGER.info(
                "Migrating legacy ZRL invoice signature uniqueness constraint"
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO consumed_zrl_signatures (
                    tx_signature,
                    invoice_token,
                    telegram_user_id
                )
                SELECT tx_signature, invoice_token, telegram_user_id
                FROM zrl_invoices
                WHERE status = 'paid'
                  AND tx_signature IS NOT NULL
                """
            )
            connection.execute(
                "ALTER TABLE zrl_invoices RENAME TO zrl_invoices_legacy"
            )
            connection.execute(
                """
                CREATE TABLE zrl_invoices (
                    invoice_token TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL,
                    usd_price TEXT NOT NULL,
                    zrl_price_usd TEXT NOT NULL,
                    required_zrl TEXT NOT NULL,
                    required_raw_amount TEXT NOT NULL,
                    token_decimals INTEGER NOT NULL,
                    price_source TEXT NOT NULL,
                    tx_signature TEXT,
                    status TEXT NOT NULL DEFAULT 'awaiting_tx',
                    verification_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    created_at_epoch INTEGER NOT NULL,
                    expires_at_epoch INTEGER NOT NULL,
                    reviewed_at TEXT,
                    paid_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO zrl_invoices (
                    invoice_token,
                    telegram_user_id,
                    usd_price,
                    zrl_price_usd,
                    required_zrl,
                    required_raw_amount,
                    token_decimals,
                    price_source,
                    tx_signature,
                    status,
                    verification_error,
                    created_at,
                    created_at_epoch,
                    expires_at_epoch,
                    reviewed_at,
                    paid_at
                )
                SELECT
                    invoice_token,
                    telegram_user_id,
                    usd_price,
                    zrl_price_usd,
                    required_zrl,
                    required_raw_amount,
                    token_decimals,
                    price_source,
                    tx_signature,
                    status,
                    verification_error,
                    created_at,
                    created_at_epoch,
                    expires_at_epoch,
                    reviewed_at,
                    paid_at
                FROM zrl_invoices_legacy
                """
            )
            connection.execute("DROP TABLE zrl_invoices_legacy")


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
            (
                photo_token,
                telegram_user_id,
            ),
        ).fetchone()

    if row is None:
        return None

    return (
        str(row[0]),
        str(row[1]) if row[1] is not None else None,
    )


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
            (
                telegram_user_id,
                FREE_GENERATIONS_PER_USER,
            ),
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


def get_remaining_generations(
    telegram_user_id: int,
) -> int:
    """Return remaining free and bonus generations."""

    with sqlite3.connect(USAGE_DB_PATH) as connection:
        row = connection.execute(
            """
            SELECT generations_used, bonus_generations
            FROM user_usage
            WHERE telegram_user_id = ?
            """,
            (telegram_user_id,),
        ).fetchone()

    if row is None:
        return FREE_GENERATIONS_PER_USER

    standard_remaining = max(
        0,
        FREE_GENERATIONS_PER_USER - int(row[0]),
    )

    return standard_remaining + int(row[1])


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

    if not (
        row[0] == "paid"
        or row[1] == "unlimited"
        or bool(row[2])
    ):
        return False

    if not row[3]:
        return True

    try:
        return datetime.fromisoformat(
            str(row[3])
        ) > utc_now()

    except ValueError:
        LOGGER.warning(
            "Invalid subscription expiry for Telegram user %s",
            telegram_user_id,
        )
        return False


def mark_subscription_expiry_warnings(
    now: datetime | None = None,
) -> list[int]:
    """Mark eligible users as notified and return their IDs."""

    current = now or utc_now()
    current_iso = current.isoformat(timespec="seconds")
    warning_limit = (
        current + timedelta(days=3)
    ).isoformat(timespec="seconds")

    with sqlite3.connect(
        USAGE_DB_PATH,
        timeout=30,
        isolation_level="IMMEDIATE",
    ) as connection:
        rows = connection.execute(
            """
            SELECT telegram_user_id
            FROM user_usage
            WHERE is_pro = 1
              AND subscription_expiry IS NOT NULL
              AND subscription_expiry > ?
              AND subscription_expiry <= ?
              AND notified_expiry = 0
            """,
            (
                current_iso,
                warning_limit,
            ),
        ).fetchall()

        for (telegram_user_id,) in rows:
            connection.execute(
                """
                UPDATE user_usage
                SET notified_expiry = 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_user_id = ?
                  AND is_pro = 1
                  AND notified_expiry = 0
                """,
                (telegram_user_id,),
            )

    return [int(row[0]) for row in rows]


def process_expired_subscriptions(
    now: datetime | None = None,
) -> list[tuple[int, str, float | None]]:
    """Renew or downgrade expired users atomically."""

    current = now or utc_now()
    current_iso = current.isoformat(timespec="seconds")
    renewed_expiry = (
        current + timedelta(days=PRO_SUBSCRIPTION_DAYS)
    ).isoformat(timespec="seconds")

    results: list[tuple[int, str, float | None]] = []

    with sqlite3.connect(
        USAGE_DB_PATH,
        timeout=30,
        isolation_level="IMMEDIATE",
    ) as connection:
        expired_users = connection.execute(
            """
            SELECT telegram_user_id
            FROM user_usage
            WHERE is_pro = 1
              AND subscription_expiry IS NOT NULL
              AND subscription_expiry <= ?
            """,
            (current_iso,),
        ).fetchall()

        for (telegram_user_id,) in expired_users:
            renewed = connection.execute(
                """
                UPDATE user_usage
                SET zrl_balance = zrl_balance - ?,
                    subscription_expiry = ?,
                    notified_expiry = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_user_id = ?
                  AND is_pro = 1
                  AND subscription_expiry IS NOT NULL
                  AND subscription_expiry <= ?
                  AND auto_renew = 1
                  AND zrl_balance >= ?
                """,
                (
                    ZRL_RENEWAL_COST,
                    renewed_expiry,
                    telegram_user_id,
                    current_iso,
                    ZRL_RENEWAL_COST,
                ),
            )

            if renewed.rowcount == 1:
                balance = connection.execute(
                    """
                    SELECT zrl_balance
                    FROM user_usage
                    WHERE telegram_user_id = ?
                    """,
                    (telegram_user_id,),
                ).fetchone()[0]

                results.append(
                    (
                        int(telegram_user_id),
                        "renewed",
                        float(balance),
                    )
                )
                continue

            downgraded = connection.execute(
                """
                UPDATE user_usage
                SET is_pro = 0,
                    subscription_status = 'free',
                    subscription_plan = 'free',
                    subscription_expiry = NULL,
                    notified_expiry = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_user_id = ?
                  AND is_pro = 1
                  AND subscription_expiry IS NOT NULL
                  AND subscription_expiry <= ?
                """,
                (
                    telegram_user_id,
                    current_iso,
                ),
            )

            if downgraded.rowcount == 1:
                results.append(
                    (
                        int(telegram_user_id),
                        "expired",
                        None,
                    )
                )

    return results


def save_crypto_invoice(
    invoice_id: int,
    telegram_user_id: int,
) -> None:
    """Persist a Crypto Pay invoice."""

    with sqlite3.connect(USAGE_DB_PATH) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO crypto_invoices
                (invoice_id, telegram_user_id, status)
            VALUES (?, ?, 'active')
            """,
            (
                invoice_id,
                telegram_user_id,
            ),
        )


def mark_invoice_status(
    invoice_id: int,
    status: str,
) -> None:
    """Persist the latest Crypto Pay status."""

    with sqlite3.connect(USAGE_DB_PATH) as connection:
        connection.execute(
            """
            UPDATE crypto_invoices
            SET status = ?
            WHERE invoice_id = ?
            """,
            (
                status,
                invoice_id,
            ),
        )


def mark_user_paid(
    telegram_user_id: int,
    invoice_id: int,
) -> None:
    """Grant unlimited access after Crypto Pay confirms payment."""

    with sqlite3.connect(USAGE_DB_PATH) as connection:
        expiry = (
            utc_now()
            + timedelta(days=PRO_SUBSCRIPTION_DAYS)
        ).isoformat(timespec="seconds")

        connection.execute(
            """
            INSERT OR IGNORE INTO user_usage
                (telegram_user_id, generations_used)
            VALUES (?, 0)
            """,
            (telegram_user_id,),
        )

        connection.execute(
            """
            UPDATE user_usage
            SET subscription_status = 'paid',
                subscription_plan = 'unlimited',
                is_pro = 1,
                paid_at = CURRENT_TIMESTAMP,
                crypto_invoice_id = ?,
                subscription_expiry = ?,
                notified_expiry = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_user_id = ?
            """,
            (
                invoice_id,
                expiry,
                telegram_user_id,
            ),
        )

        connection.execute(
            """
            UPDATE crypto_invoices
            SET status = 'paid',
                paid_at = CURRENT_TIMESTAMP
            WHERE invoice_id = ?
            """,
            (invoice_id,),
        )


def grant_pro_access(
    telegram_user_id: int,
) -> None:
    """Manually grant paid, unlimited Pro access."""

    with sqlite3.connect(USAGE_DB_PATH) as connection:
        expiry = (
            utc_now()
            + timedelta(days=PRO_SUBSCRIPTION_DAYS)
        ).isoformat(timespec="seconds")

        connection.execute(
            """
            INSERT OR IGNORE INTO user_usage
                (telegram_user_id, generations_used)
            VALUES (?, 0)
            """,
            (telegram_user_id,),
        )

        connection.execute(
            """
            UPDATE user_usage
            SET is_pro = 1,
                subscription_status = 'paid',
                subscription_plan = 'unlimited',
                paid_at = CURRENT_TIMESTAMP,
                subscription_expiry = ?,
                notified_expiry = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_user_id = ?
            """,
            (
                expiry,
                telegram_user_id,
            ),
        )


async def notify_admin_about_zrl_claim(
    bot: Bot,
    user: types.User,
    invoice_token: str,
    tx_signature: str,
    required_zrl: str,
    reason: str,
) -> bool:
    """Notify the administrator about a ZRL claim requiring manual review."""

    admin_id_str = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
    if not admin_id_str:
        return False

    try:
        admin_id = int(admin_id_str)
    except ValueError:
        LOGGER.error("ADMIN_TELEGRAM_ID must be a numeric Telegram user ID")
        return False

    username = f"@{user.username}" if user.username else "(no username)"
    alert = (
        "🚨 ZRL payment requires manual review\n\n"
        f"Telegram ID: {user.id}\n"
        f"Username: {username}\n"
        f"Expected: {required_zrl} ZRL\n"
        f"Signature: {tx_signature}\n"
        f"Explorer: {SOLANA_EXPLORER_URL}{tx_signature}\n"
        f"Automatic verification: {reason}"
    )
    try:
        await bot.send_message(
            admin_id,
            alert,
            parse_mode=None,
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="✅ Одобрить ZRL",
                            callback_data=f"zrl_approve:{invoice_token}",
                        ),
                        types.InlineKeyboardButton(
                            text="❌ Отклонить",
                            callback_data=f"zrl_reject:{invoice_token}",
                        ),
                    ]
                ]
            ),
        )
        return True
    except Exception:
        LOGGER.exception(
            "Could not send ZRL claim %s to admin",
            invoice_token,
        )
        return False


async def crypto_api_request(
    method: str,
    endpoint: str,
    *,
    json_body: dict[str, object] | None = None,
    params: dict[str, str] | None = None,
) -> object:
    """Call Crypto Pay API without exposing the API token in logs."""

    api_token = get_required_env("CRYPTOBOT_API_TOKEN")
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"Crypto-Pay-API-Token": api_token}

    async with aiohttp.ClientSession(
        base_url=CRYPTOBOT_API_BASE,
        timeout=timeout,
        headers=headers,
    ) as session:
        async with session.request(
            method,
            endpoint,
            json=json_body,
            params=params,
        ) as response:
            raw_body = await response.text()
            try:
                response_body = json.loads(raw_body)
            except json.JSONDecodeError:
                response_body = {"error": {"name": raw_body[:200]}}

    if (
        response.status >= 400
        or not isinstance(response_body, dict)
        or not response_body.get("ok")
    ):
        description = (
            response_body.get("error", {}).get("name", "Unknown Crypto Pay error")
            if isinstance(response_body, dict)
            else "Invalid Crypto Pay response"
        )
        raise RuntimeError(
            f"Crypto Pay API request failed ({response.status}): {description}"
        )

    return response_body.get("result")


async def create_crypto_invoice(
    telegram_user_id: int,
) -> tuple[int, str]:
    """Create a real 10 USDT CryptoBot invoice and return its payment URL."""

    invoice = await crypto_api_request(
        "POST",
        "createInvoice",
        json_body={
            "asset": "USDT",
            "amount": CRYPTO_INVOICE_AMOUNT,
            "description": (
                "Zer0Life Commerce AI — unlimited access for 1 month"
            ),
            "payload": f"zerolife:{telegram_user_id}",
            "expires_in": CRYPTO_INVOICE_TTL_SECONDS,
            "allow_comments": False,
            "allow_anonymous": False,
        },
    )
    if not isinstance(invoice, dict):
        raise RuntimeError("Crypto Pay returned an invalid invoice.")

    invoice_id = invoice.get("invoice_id")
    payment_url = (
        invoice.get("bot_invoice_url")
        or invoice.get("pay_url")
        or invoice.get("mini_app_invoice_url")
    )
    if not isinstance(invoice_id, int) or not isinstance(payment_url, str):
        raise RuntimeError("Crypto Pay invoice did not include a payment URL.")

    await asyncio.to_thread(
        save_crypto_invoice,
        invoice_id,
        telegram_user_id,
    )
    return invoice_id, payment_url


async def create_crypto_donation_invoice(
    telegram_user_id: int,
) -> str:
    """Create a 10 USDT CryptoBot invoice for a voluntary donation."""

    invoice = await crypto_api_request(
        "POST",
        "createInvoice",
        json_body={
            "asset": "USDT",
            "amount": CRYPTO_DONATION_AMOUNT,
            "description": "Support Zer0Life Labs donation",
            "payload": f"donation:{telegram_user_id}",
            "expires_in": CRYPTO_INVOICE_TTL_SECONDS,
            "allow_comments": False,
            "allow_anonymous": False,
        },
    )
    if not isinstance(invoice, dict):
        raise RuntimeError("Crypto Pay returned an invalid donation invoice.")

    payment_url = (
        invoice.get("bot_invoice_url")
        or invoice.get("pay_url")
        or invoice.get("mini_app_invoice_url")
    )
    if not isinstance(payment_url, str):
        raise RuntimeError(
            "Crypto Pay donation invoice did not include a payment URL."
        )
    return payment_url


async def monitor_crypto_invoice(
    bot: Bot,
    invoice_id: int,
    telegram_user_id: int,
    chat_id: int,
) -> None:
    """Poll Crypto Pay until the invoice is paid, expired, or cancelled."""

    deadline = time.monotonic() + CRYPTO_INVOICE_TTL_SECONDS
    while time.monotonic() < deadline:
        try:
            invoices = await crypto_api_request(
                "GET",
                "getInvoices",
                params={"invoice_ids": str(invoice_id)},
            )
            invoice = (
                invoices[0]
                if isinstance(invoices, list) and invoices
                else None
            )
            invoice_status = (
                invoice.get("status")
                if isinstance(invoice, dict)
                else None
            )

            if invoice_status == "paid":
                await asyncio.to_thread(
                    mark_user_paid,
                    telegram_user_id,
                    invoice_id,
                )
                LOGGER.info(
                    "Crypto payment confirmed: telegram_user_id=%s invoice_id=%s",
                    telegram_user_id,
                    invoice_id,
                )
                try:
                    await bot.send_message(
                        chat_id,
                        "✅ Оплата получена!\n\n"
                        "Статус обновлён: <b>paid / unlimited</b>. "
                        "Теперь можно создавать листинги без лимита.",
                    )
                except Exception:
                    LOGGER.warning(
                        "Could not send payment confirmation for invoice %s",
                        invoice_id,
                    )
                return

            if invoice_status in {"expired", "cancelled"}:
                await asyncio.to_thread(
                    mark_invoice_status,
                    invoice_id,
                    str(invoice_status),
                )
                await bot.send_message(
                    chat_id,
                    "⌛ Счёт истёк или был отменён. Создай новый счёт, "
                    "если хочешь продолжить.",
                )
                return

        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "Could not check Crypto Pay invoice %s",
                invoice_id,
            )

        await asyncio.sleep(CRYPTO_POLL_INTERVAL_SECONDS)

    await asyncio.to_thread(
        mark_invoice_status,
        invoice_id,
        "expired",
    )

    try:
        await bot.send_message(
            chat_id,
            "⌛ Срок действия счёта истёк. Создай новый счёт, "
            "если хочешь продолжить.",
        )
    except Exception:
        LOGGER.warning(
            "Could not send invoice expiry message for invoice %s",
            invoice_id,
        )


def start_invoice_monitor(
    bot: Bot,
    invoice_id: int,
    telegram_user_id: int,
    chat_id: int,
) -> None:
    """Start a tracked background task for one pending invoice."""

    task = asyncio.create_task(
        monitor_crypto_invoice(
            bot,
            invoice_id,
            telegram_user_id,
            chat_id,
        )
    )
    INVOICE_MONITOR_TASKS.add(task)
    task.add_done_callback(INVOICE_MONITOR_TASKS.discard)


async def resume_pending_invoice_monitors(bot: Bot) -> None:
    """Resume active invoice checks when the bot starts again."""

    if not os.getenv("CRYPTOBOT_API_TOKEN", "").strip():
        LOGGER.warning(
            "CRYPTOBOT_API_TOKEN is not configured; pending invoices are paused"
        )
        return

    pending_invoices = await asyncio.to_thread(
        get_pending_crypto_invoices,
    )

    for invoice_id, telegram_user_id in pending_invoices:
        start_invoice_monitor(
            bot,
            invoice_id,
            telegram_user_id,
            telegram_user_id,
        )


async def resume_pending_zrl_verifications(bot: Bot) -> None:
    """Finish ZRL verifications interrupted by a process restart."""

    pending = await asyncio.to_thread(
        get_verifying_zrl_invoices,
    )

    for (
        invoice_token,
        telegram_user_id,
        tx_signature,
        required_zrl,
        required_raw_amount,
        created_at_epoch,
        expires_at_epoch,
    ) in pending:
        try:
            await verify_zrl_transaction(
                tx_signature,
                required_raw_amount,
                created_at_epoch,
                expires_at_epoch,
            )

            user_id = await asyncio.to_thread(
                complete_zrl_payment,
                invoice_token,
            )

            if user_id is not None:
                await bot.send_message(
                    user_id,
                    "🎉 ZRL-платёж подтверждён после перезапуска. "
                    "Ваш Pro-доступ активирован!",
                    parse_mode=None,
                )

        except Exception as exc:
            reason = str(exc)
            LOGGER.exception(
                "Could not resume ZRL verification %s",
                invoice_token,
            )

            await asyncio.to_thread(
                mark_zrl_manual_review,
                invoice_token,
                reason,
            )

            fallback_user = types.User(
                id=telegram_user_id,
                is_bot=False,
                first_name="ZRL payer",
            )

            await notify_admin_about_zrl_claim(
                bot,
                fallback_user,
                invoice_token,
                tx_signature,
                required_zrl,
                reason,
            )


async def subscription_auto_renew_checker(bot: Bot) -> None:
    """Warn about expiring Pro and renew or downgrade users every 12 hours."""

    while True:
        try:
            now = utc_now()

            warning_user_ids = await asyncio.to_thread(
                mark_subscription_expiry_warnings,
                now,
            )

            for user_id in warning_user_ids:
                try:
                    await bot.send_message(
                        user_id,
                        "⚠️ Ваша Pro-подписка истекает в течение 3 дней.\n\n"
                        "При включённом автопродлении и балансе от "
                        f"{ZRL_RENEWAL_COST:,.0f} ZRL она продлится автоматически.",
                        parse_mode=None,
                    )
                except Exception:
                    LOGGER.exception(
                        "Could not send subscription expiry warning to %s",
                        user_id,
                    )

            changes = await asyncio.to_thread(
                process_expired_subscriptions,
                now,
            )

            for user_id, outcome, balance in changes:
                try:
                    if outcome == "renewed":
                        await bot.send_message(
                            user_id,
                            "🎉 Ваша Pro-подписка автоматически продлена "
                            f"на {PRO_SUBSCRIPTION_DAYS} дней.\n"
                            f"Списано: {ZRL_RENEWAL_COST:,.0f} ZRL.\n"
                            f"Остаток баланса: {balance:,.6f} ZRL.",
                            parse_mode=None,
                        )
                    else:
                        await bot.send_message(
                            user_id,
                            "❌ Срок действия Pro-подписки истёк.\n\n"
                            "Недостаточно ZRL или автопродление отключено. "
                            "Доступ переведён на бесплатный план. "
                            "Выберите оплату через /start.",
                            parse_mode=None,
                        )
                except Exception:
                    LOGGER.exception(
                        "Could not notify user %s about subscription change",
                        user_id,
                    )

        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Subscription renewal checker failed")

        await asyncio.sleep(12 * 60 * 60)


def start_subscription_auto_renew_checker(bot: Bot) -> None:
    """Start one tracked subscription checker task."""

    task = asyncio.create_task(
        subscription_auto_renew_checker(bot)
    )
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)

    LOGGER.info(
        "Subscription expiry checker started: interval=12h renewal_cost=%.0f ZRL",
        ZRL_RENEWAL_COST,
    )


def build_clients() -> tuple[Bot, AsyncOpenAI]:
    """Create API clients only after validating required secrets."""

    telegram_token = get_required_env("TELEGRAM_BOT_TOKEN")
    openai_api_key = get_required_env("OPENAI_API_KEY")

    bot = Bot(
        token=telegram_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    openai_client = AsyncOpenAI(
        api_key=openai_api_key,
    )

    return bot, openai_client


async def send_referral_menu(
    message: types.Message,
    telegram_user_id: int,
) -> None:
    """Show the referral link and current partner statistics."""

    referral_count, bonus_generations = await asyncio.to_thread(
        get_referral_stats,
        telegram_user_id,
    )

    referral_link = (
        f"https://t.me/{BOT_USERNAME}?start=ref_{telegram_user_id}"
    )

    await message.answer(
        "🤝 Партнёрская программа\n\n"
        f"Ваша уникальная ссылка:\n{referral_link}\n\n"
        f"👥 Приглашено пользователей: {referral_count}\n"
        f"🎁 Доступно бонусных генераций: {bonus_generations}\n\n"
        "Скоро: 20% комиссионных с каждого купленного Pro-статуса "
        "вашими рефералами!",
        parse_mode=None,
    )


@ROUTER.message(Command("start"))
async def start_handler(message: types.Message) -> None:
    telegram_user_id = message.from_user.id
    referrer_id: int | None = None

    command_parts = (
        message.text or ""
    ).split(maxsplit=1)

    if (
        len(command_parts) == 2
        and command_parts[1].startswith("ref_")
    ):
        try:
            referrer_id = int(
                command_parts[1].removeprefix("ref_")
            )
        except ValueError:
            LOGGER.info(
                "Ignored invalid referral payload from Telegram user %s",
                telegram_user_id,
            )

    referral_rewarded = await asyncio.to_thread(
        register_user,
        telegram_user_id,
        referrer_id,
    )

    if referral_rewarded and referrer_id is not None:
        try:
            await message.bot.send_message(
                referrer_id,
                "🎉 Новый реферал зарегистрировался! "
                "Вам начислена 1 бесплатная генерация.",
                parse_mode=None,
            )
        except Exception:
            LOGGER.exception(
                "Could not notify referrer %s about new user %s",
                referrer_id,
                telegram_user_id,
            )

    await message.answer(
        "👋 Я <b>Zer0Life Commerce AI</b>.\n\n"
        "📸 Отправь фото товара, и я создам:\n"
        "• SEO-заголовок на EN и PL\n"
        "• Продающее описание на EN и PL\n"
        "• 13 тегов для Etsy/Allegro\n"
        "• Удаление или замену фона\n"
        "• Примерку товара на модели\n\n"
        "Команда /help покажет подсказку.",
        reply_markup=main_menu_keyboard(),
    )


@ROUTER.message(Command("ref"))
async def referral_command_handler(
    message: types.Message,
) -> None:
    await send_referral_menu(
        message,
        message.from_user.id,
    )


@ROUTER.callback_query(F.data == "referral:menu")
async def referral_menu_handler(
    callback: types.CallbackQuery,
) -> None:
    await callback.answer()

    if callback.message is not None:
        await send_referral_menu(
            callback.message,
            callback.from_user.id,
        )


@ROUTER.message(Command("help"))
async def help_handler(
    message: types.Message,
) -> None:
    await message.answer(
        "Отправь одно или несколько фото товара одним сообщением. "
        "После загрузки выбери SEO-карточку, удаление/замену фона "
        "или примерку на модели.\n\n"
        "Для более точного результата добавь в подписи к фото материал, "
        "размер, цвет и назначение товара."
    )


@ROUTER.message(F.photo)
async def photo_upload_handler(
    message: types.Message,
) -> None:
    """Save an uploaded photo and offer available actions."""

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


async def process_seo_listing(
    message: types.Message,
    bot: Bot,
    openai_client: AsyncOpenAI,
    telegram_user_id: int,
    telegram_file_id: str,
    caption: str | None,
) -> None:
    """Generate the marketplace listing for a selected photo."""

    has_unlimited_access = await asyncio.to_thread(
        is_user_paid,
        telegram_user_id,
    )

    generation_source: str | None = (
        "unlimited"
        if has_unlimited_access
        else None
    )

    if generation_source is None:
        generation_source = await asyncio.to_thread(
            reserve_free_generation,
            telegram_user_id,
        )

    if generation_source is None:
        await message.answer(
            "⛔ Бесплатный лимит исчерпан.\n\n"
            f"На одного пользователя доступно "
            f"{FREE_GENERATIONS_PER_USER} генерации фото.\n\n"
            "Выбери способ оплаты, чтобы продолжить:",
            reply_markup=subscription_keyboard(),
        )
        return

    status_message: types.Message | None = None
    generation_succeeded = False

    try:
        status_message = await message.answer(
            "⏳ Анализирую фото и создаю листинг..."
        )

        file_info = await bot.get_file(
            telegram_file_id,
        )

        image_stream = io.BytesIO()
        await bot.download_file(
            file_info.file_path,
            destination=image_stream,
        )

        base64_image = base64.b64encode(
            image_stream.getvalue(),
        ).decode("ascii")

        user_prompt = LISTING_PROMPT

        if caption:
            user_prompt += (
                "\n\nAdditional seller-provided context. Use it when relevant, "
                "but do not contradict what is visible in the photo:\n"
                f"{caption[:2000]}"
            )

        response = await asyncio.wait_for(
            openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": user_prompt,
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": (
                                        "data:image/jpeg;base64,"
                                        f"{base64_image}"
                                    ),
                                },
                            },
                        ],
                    },
                ],
                max_tokens=1200,
            ),
            timeout=OPENAI_VISION_TIMEOUT_SECONDS,
        )

        result_text = response.choices[0].message.content

        if not result_text:
            raise RuntimeError(
                "OpenAI returned an empty response."
            )

        generation_succeeded = True

        if status_message:
            try:
                await status_message.delete()
            except Exception:
                LOGGER.warning(
                    "Could not delete the processing status message"
                )

        listing_token = await asyncio.to_thread(
            save_generated_listing,
            telegram_user_id,
            result_text,
            telegram_file_id,
        )

        listing_chunks = split_for_telegram(
            result_text,
        )

        for index, chunk in enumerate(listing_chunks):
            is_last_chunk = (
                index == len(listing_chunks) - 1
            )

            await message.answer(
                chunk,
                parse_mode=None,
                reply_markup=(
                    translation_keyboard(listing_token)
                    if is_last_chunk
                    else None
                ),
            )

        if has_unlimited_access:
            await message.answer(
                "♾️ Статус: paid / unlimited",
                parse_mode=None,
            )
        else:
            remaining = await asyncio.to_thread(
                get_remaining_generations,
                telegram_user_id,
            )

            await message.answer(
                f"📊 Осталось бесплатных генераций: {remaining}",
                parse_mode=None,
            )

    except Exception:
        if (
            not generation_succeeded
            and generation_source in {"standard", "bonus"}
        ):
            await asyncio.to_thread(
                refund_free_generation,
                telegram_user_id,
                generation_source,
            )

        LOGGER.exception(
            "Failed to create a listing for Telegram user %s",
            telegram_user_id,
        )

        error_message = (
            "⚠️ Ошибка при генерации карточки товара. "
            "Пожалуйста, отправьте фото еще раз."
        )

        if status_message:
            try:
                await status_message.edit_text(
                    error_message,
                )
            except Exception:
                LOGGER.exception(
                    "Could not edit the generation status message for user %s",
                    telegram_user_id,
                )
                await message.answer(
                    error_message,
                )
        else:
            await message.answer(
                error_message,
            )


async def process_image_edit(
    message: types.Message,
    bot: Bot,
    openai_client: AsyncOpenAI,
    telegram_user_id: int,
    telegram_file_id: str,
    *,
    prompt: str,
    output_caption: str,
    transparent_background: bool = False,
) -> None:
    """Run one credit-backed OpenAI image edit and send the result."""

    has_unlimited_access = await asyncio.to_thread(
        is_user_paid,
        telegram_user_id,
    )

    generation_source: str | None = (
        "unlimited"
        if has_unlimited_access
        else None
    )

    if generation_source is None:
        generation_source = await asyncio.to_thread(
            reserve_free_generation,
            telegram_user_id,
        )

    if generation_source is None:
        await message.answer(
            "⛔ Бесплатный лимит исчерпан.\n\n"
            "Для обработки изображения нужна 1 генерация. "
            "Выбери способ оплаты, чтобы продолжить:",
            reply_markup=subscription_keyboard(),
        )
        return

    status_message: types.Message | None = None
    processing_succeeded = False

    try:
        status_message = await message.answer(
            "⏳ Обрабатываю изображение..."
        )

        file_info = await bot.get_file(
            telegram_file_id,
        )

        image_stream = io.BytesIO()
        await bot.download_file(
            file_info.file_path,
            destination=image_stream,
        )

        image_bytes = image_stream.getvalue()

        if not image_bytes:
            raise RuntimeError(
                "Telegram returned an empty image."
            )

        edit_options: dict[str, object] = {
            "model": OPENAI_IMAGE_MODEL,
            "image": (
                "product.jpg",
                image_bytes,
                "image/jpeg",
            ),
            "prompt": prompt,
            "input_fidelity": "high",
            "quality": "high",
            "size": "auto",
            "output_format": "png",
        }

        if transparent_background:
            edit_options["background"] = "transparent"

        response = await asyncio.wait_for(
            openai_client.images.edit(
                **edit_options,
            ),
            timeout=OPENAI_IMAGE_TIMEOUT_SECONDS,
        )

        encoded_image = (
            response.data[0].b64_json
            if response.data
            else None
        )

        if not encoded_image:
            raise RuntimeError(
                "OpenAI returned an empty edited image."
            )

        result_bytes = base64.b64decode(
            encoded_image,
        )

        output_file = types.BufferedInputFile(
            result_bytes,
            filename="zerolife-edited-product.png",
        )

        if transparent_background:
            await message.answer_document(
                output_file,
                caption=output_caption,
                parse_mode=None,
            )
        else:
            await message.answer_photo(
                output_file,
                caption=output_caption,
                parse_mode=None,
            )

        processing_succeeded = True

        if status_message is not None:
            try:
                await status_message.delete()
            except Exception:
                LOGGER.warning(
                    "Could not delete image processing status"
                )

        if not has_unlimited_access:
            remaining = await asyncio.to_thread(
                get_remaining_generations,
                telegram_user_id,
            )

            await message.answer(
                f"📊 Осталось бесплатных генераций: {remaining}",
                parse_mode=None,
            )

    except Exception:
        if (
            not processing_succeeded
            and generation_source in {"standard", "bonus"}
        ):
            await asyncio.to_thread(
                refund_free_generation,
                telegram_user_id,
                generation_source,
            )

        LOGGER.exception(
            "Failed to edit an image for Telegram user %s",
            telegram_user_id,
        )

        error_message = (
            "⚠️ Не удалось обработать изображение. "
            "Генерация возвращена, попробуйте ещё раз."
        )

        if status_message is not None:
            try:
                await status_message.edit_text(
                    error_message,
                )
            except Exception:
                LOGGER.exception(
                    "Could not edit image status for Telegram user %s",
                    telegram_user_id,
                )
                await message.answer(
                    error_message,
                )
        else:
            await message.answer(
                error_message,
            )


@ROUTER.callback_query(F.data.startswith("photo:"))
async def photo_action_handler(
    callback: types.CallbackQuery,
    bot: Bot,
    openai_client: AsyncOpenAI,
) -> None:
    """Dispatch an uploaded photo to SEO or image processing."""

    callback_data = callback.data or ""
    parts = callback_data.split(
        ":",
        maxsplit=2,
    )

    if len(parts) != 3:
        await callback.answer(
            "Некорректное действие.",
            show_alert=True,
        )
        return

    _, action, photo_token = parts

    pending_photo = await asyncio.to_thread(
        get_pending_photo,
        photo_token,
        callback.from_user.id,
    )

    if pending_photo is None:
        await callback.answer(
            "Фото не найдено. Отправьте его ещё раз.",
            show_alert=True,
        )
        return

    if callback.message is None:
        await callback.answer()
        return

    telegram_file_id, caption = pending_photo

    if action == "background":
        await callback.answer(
            "Выберите стиль фона.",
        )

        await callback.message.answer(
            "🌄 Выберите новый фон:",
            reply_markup=background_style_keyboard(
                photo_token,
            ),
        )
        return

    await callback.answer()

    if action == "seo":
        await process_seo_listing(
            callback.message,
            bot,
            openai_client,
            callback.from_user.id,
            telegram_file_id,
            caption,
        )
        return

    if action == "remove":
        await process_image_edit(
            callback.message,
            bot,
            openai_client,
            callback.from_user.id,
            telegram_file_id,
            prompt=(
                "Isolate the exact product from the supplied photo. Preserve "
                "its shape, color, texture, labels, proportions, and all visible "
                "details. Remove every background element and produce a clean "
                "transparent PNG with crisp natural edges. Do not redesign or "
                "add anything to the product."
            ),
            output_caption="✂️ Фон удалён.",
            transparent_background=True,
        )
        return

    if action == "model":
        await process_image_edit(
            callback.message,
            bot,
            openai_client,
            callback.from_user.id,
            telegram_file_id,
            prompt=(
                "Create a realistic premium ecommerce fashion photograph with "
                "an adult professional model naturally wearing the supplied "
                "product if it is wearable, or naturally holding it if it is "
                "an accessory or object. Preserve the product's exact design, "
                "color, logos, texture, proportions, and visible details. Use "
                "natural lighting, believable scale and anatomy, and a polished "
                "commercial composition. The product must remain the focal point."
            ),
            output_caption="🧍 Примерка на модель готова.",
        )
        return

    await callback.message.answer(
        "Неизвестное действие. Отправьте фото ещё раз."
    )


@ROUTER.callback_query(F.data.startswith("photo_bg:"))
async def background_style_handler(
    callback: types.CallbackQuery,
    bot: Bot,
    openai_client: AsyncOpenAI,
) -> None:
    """Replace a product background using a selected visual style."""

    callback_data = callback.data or ""
    parts = callback_data.split(
        ":",
        maxsplit=2,
    )

    if len(parts) != 3:
        await callback.answer(
            "Некорректный стиль.",
            show_alert=True,
        )
        return

    _, style, photo_token = parts

    styles = {
        "studio": (
            "a refined modern photography studio with soft professional "
            "lighting, subtle depth, and a premium ecommerce campaign feel"
        ),
        "living": (
            "an inviting aesthetic living room with tasteful contemporary "
            "decor, warm natural light, and realistic spatial depth"
        ),
        "street": (
            "a stylish outdoor streetwear setting in a modern city, with "
            "natural daylight, subtle urban texture, and editorial energy"
        ),
        "neutral": (
            "a minimalist neutral setting with warm beige and stone tones, "
            "soft diffused light, gentle shadows, and uncluttered composition"
        ),
    }

    style_description = styles.get(style, styles["studio"])
    
    pending_photo = await asyncio.to_thread(
        get_pending_photo,
        photo_token,
        callback.from_user.id,
    )

    if pending_photo is None:
        await callback.answer("Фото не найдено.", show_alert=True)
        return

    telegram_file_id, _ = pending_photo
    await callback.answer()

    await process_image_edit(
        callback.message,
        bot,
        openai_client,
        callback.from_user.id,
        telegram_file_id,
        prompt=(
            f"Place the exact product from the supplied photo into {style_description}. "
            "Preserve the product's shape, color, texture, labels, and proportions completely. "
            "Seamlessly integrate it with matching realistic contact shadows and lighting."
        ),
        output_caption=f"🌄 Фон изменён ({style}).",
    )


@ROUTER.callback_query(F.data.startswith("approve_pro_"))
async def approve_pro_handler(callback: types.CallbackQuery) -> None:
    """Handle administrator approval for Pro access requests."""

    admin_id_str = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
    if not admin_id_str:
        await callback.answer("ADMIN_TELEGRAM_ID не настроен.", show_alert=True)
        return

    try:
        admin_id = int(admin_id_str)
    except ValueError:
        LOGGER.error("ADMIN_TELEGRAM_ID must be a numeric Telegram user ID")
        await callback.answer(
            "ADMIN_TELEGRAM_ID настроен неверно.",
            show_alert=True,
        )
        return

    if callback.from_user.id != admin_id:
        LOGGER.warning(
            "Unauthorized Pro approval attempt by Telegram user %s",
            callback.from_user.id,
        )
        await callback.answer(
            "Недостаточно прав.",
            show_alert=True,
        )
        return

    callback_data = callback.data or ""
    raw_user_id = callback_data.removeprefix("approve_pro_")

    try:
        user_id = int(raw_user_id)
    except ValueError:
        await callback.answer(
            "Некорректный ID пользователя.",
            show_alert=True,
        )
        return

    await asyncio.to_thread(
        grant_pro_access,
        user_id,
    )

    LOGGER.info(
        "Admin %s granted Pro access to Telegram user %s",
        admin_id,
        user_id,
    )

    await callback.answer(
        "Pro-доступ выдан.",
    )

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=None,
            )
        except Exception:
            LOGGER.warning(
                "Could not remove the Pro approval button"
            )

    try:
        await callback.bot.send_message(
            admin_id,
            f"Pro-доступ успешно выдан для ID: {user_id}",
            parse_mode=None,
        )
    except Exception:
        LOGGER.exception(
            "Pro access was granted, but admin %s could not be notified",
            user_id,
        )

    try:
        await callback.bot.send_message(
            user_id,
            "🎉 Ваш Pro-доступ успешно активирован! "
            "Теперь у вас неограниченный доступ.",
            parse_mode=None,
        )
    except Exception:
        LOGGER.exception(
            "Pro access was granted, but user %s could not be notified",
            user_id,
        )


@ROUTER.message(F.text)
async def zrl_signature_handler(
    message: types.Message,
) -> None:
    """Verify a submitted Solana signature for the user's ZRL invoice."""

    submitted_parts = (
        message.text or ""
    ).strip().split()

    invoice_token_hint: str | None = None

    if (
        len(submitted_parts) == 2
        and len(submitted_parts[0]) == 16
        and all(
            character in "0123456789abcdef"
            for character in submitted_parts[0]
        )
    ):
        invoice_token_hint, tx_signature = submitted_parts

    elif len(submitted_parts) == 1:
        tx_signature = submitted_parts[0]

    else:
        tx_signature = ""

    base58_alphabet = set(
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        "abcdefghijkmnopqrstuvwxyz"
    )

    if not (
        80 <= len(tx_signature) <= 100
        and all(
            character in base58_alphabet
            for character in tx_signature
        )
    ):
        await message.answer(
            "❌ Это не похоже на Solana Transaction Signature. "
            "Скопируйте полную подпись транзакции и отправьте её "
            "одним сообщением.",
            parse_mode=None,
        )
        return

    invoice = await asyncio.to_thread(
        get_zrl_invoice_for_submission,
        message.from_user.id,
        invoice_token_hint,
    )

    if invoice is None:
        await message.answer(
            "⚠️ Подходящий ZRL-счёт не найден. Создайте новый счёт "
            "или отправьте данные в формате: "
            "<номер_счёта> <TxHash>.",
            parse_mode=None,
        )
        return

    (
        invoice_token,
        required_zrl,
        required_raw_amount,
        _,
        created_at_epoch,
        expires_at_epoch,
    ) = invoice

    claim_result = await asyncio.to_thread(
        claim_zrl_signature,
        invoice_token,
        message.from_user.id,
        tx_signature,
    )

    if claim_result == "duplicate":
        await message.answer(
            "❌ Эта транзакция уже использовалась для другого счёта.",
            parse_mode=None,
        )
        return

    if claim_result != "claimed":
        await message.answer(
            "⚠️ Этот счёт уже обрабатывается. "
            "Создайте новый счёт при необходимости.",
            parse_mode=None,
        )
        return

    status_message = await message.answer(
        "⏳ Проверяю ZRL-транзакцию в сети Solana..."
    )

    try:
        received_raw = await verify_zrl_transaction(
            tx_signature,
            required_raw_amount,
            created_at_epoch,
            expires_at_epoch,
        )

        user_id = await asyncio.to_thread(
            complete_zrl_payment,
            invoice_token,
        )

        if user_id is None:
            raise RuntimeError(
                "Invoice was already processed."
            )

        LOGGER.info(
            "Verified ZRL payment: invoice=%s user=%s received_raw=%s",
            invoice_token,
            message.from_user.id,
            received_raw,
        )

        await status_message.edit_text(
            "🎉 ZRL-платёж подтверждён в сети Solana! "
            "Ваш Pro-доступ автоматически активирован.",
            parse_mode=None,
        )

    except Exception as exc:
        reason = str(exc)

        LOGGER.exception(
            "Automatic ZRL verification failed for invoice %s",
            invoice_token,
        )

        await asyncio.to_thread(
            mark_zrl_manual_review,
            invoice_token,
            reason,
        )

        alert_sent = await notify_admin_about_zrl_claim(
            message.bot,
            message.from_user,
            invoice_token,
            tx_signature,
            required_zrl,
            reason,
        )

        if alert_sent:
            await status_message.edit_text(
                "⏳ Автоматическая проверка не завершилась. "
                "Транзакция отправлена администратору "
                "для ручной проверки.",
                parse_mode=None,
            )
        else:
            await status_message.edit_text(
                "⚠️ Автоматическая проверка не завершилась, "
                "и уведомление администратору не отправилось. "
                "Обратитесь в поддержку.",
                parse_mode=None,
            )


@ROUTER.message()
async def unsupported_message_handler(
    message: types.Message,
) -> None:
    await message.answer(
        "Пожалуйста, отправь фото товара. "
        "Для начала работы используй /start."
    )


async def main() -> None:
    logging.basicConfig(
        level=os.getenv(
            "LOG_LEVEL",
            "INFO",
        ).upper(),
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    bot, openai_client = build_clients()

    init_usage_db()

    dispatcher = Dispatcher()
    dispatcher.include_router(ROUTER)

    LOGGER.info(
        "Zer0Life Commerce AI is starting with model %s",
        OPENAI_MODEL,
    )

    await resume_pending_invoice_monitors(bot)
    await resume_pending_zrl_verifications(bot)
    start_subscription_auto_renew_checker(bot)

    try:
        await dispatcher.start_polling(
            bot,
            openai_client=openai_client,
        )

    finally:
        pending_tasks = list(
            INVOICE_MONITOR_TASKS
        )

        for task in pending_tasks:
            task.cancel()

        if pending_tasks:
            await asyncio.gather(
                *pending_tasks,
                return_exceptions=True,
            )

        background_tasks = list(
            BACKGROUND_TASKS
        )

        for task in background_tasks:
            task.cancel()

        if background_tasks:
            await asyncio.gather(
                *background_tasks,
                return_exceptions=True,
            )

        await openai_client.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        keep_alive()
        asyncio.run(main())

    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(1) from exc

    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("Bot stopped.")

