import json
import logging
import sys
import os
import asyncio
import base58
import base64

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp

from flask import Flask, request, jsonify
from flask_cors import CORS
from solana.rpc.api import Client
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import Transaction
from solders.message import Message
from solders.hash import Hash
from spl.token.instructions import transfer_checked, TransferCheckedParams, get_associated_token_address

api_app = Flask(__name__)
CORS(api_app)

TOKEN = os.getenv("BOT_TOKEN")
# Прописываем твой реальный URL с Render напрямую для надежности
RENDER_URL = "https://zer0life-ai-iz5n.onrender.com"

dp = Dispatcher()
bot = Bot(token=TOKEN) if TOKEN else None

@api_app.route('/withdraw', methods=['POST'])
def withdraw():
    try:
        data = request.json
        user_pubkey = Pubkey.from_string(data['walletAddress'])
        amount = float(data['amount'])
        
        zrl_mint_address = os.getenv('ZRL_MINT')
        private_key_base58 = os.getenv('PRIVATE_KEY')
        
        if not zrl_mint_address or not private_key_base58:
            return jsonify({"error": "Сервер не настроен (отсутствуют ключи)"}), 500

        zrl_mint = Pubkey.from_string(zrl_mint_address)
        pool_keypair = Keypair.from_bytes(base58.b58decode(private_key_base58))
        
        pool_ata = get_associated_token_address(pool_keypair.pubkey(), zrl_mint)
        user_ata = get_associated_token_address(user_pubkey, zrl_mint)
        
        transfer_ix = transfer_checked(
            TransferCheckedParams(
                program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
                source=pool_ata,
                mint=zrl_mint,
                dest=user_ata,
                owner=pool_keypair.pubkey(),
                amount=int(amount * (10**6)), 
                decimals=6
            )
        )
        
        client = Client("https://api.mainnet-beta.solana.com")
        recent_blockhash_str = client.get_latest_blockhash().value.blockhash
        recent_blockhash = Hash.from_string(str(recent_blockhash_str))
        
        message = Message([transfer_ix], user_pubkey)
        tx = Transaction(
            from_keypairs=[pool_keypair],
            message=message,
            recent_blockhash=recent_blockhash
        )
        
        serialized_tx = base64.b64encode(tx.serialize()).decode('utf-8')
        return jsonify({"transaction": serialized_tx})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@api_app.route(f'/webhook/{TOKEN}', methods=['POST'])
def telegram_webhook():
    if request.headers.get('content-type') == 'application/json':
        json_data = request.get_json()
        update = types.Update.model_validate(json_data, context={"bot": bot})
        asyncio.run(dp.feed_update(bot, update))
        return '', 200
    return 'Invalid request', 403

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏃 Запустить Zer0Life Run", web_app=WebAppInfo(url="https://wlodek2106-cyber.github.io/Zer0life_ai/"))]
    ])
    await message.answer(
        "Йоу! Добро пожаловать в <b>Zer0Life Run</b> ⚡️\n\n"
        "Тренируйся, шагай и зарабатывай токены <b>ZRL</b>!\n"
        "Нажми кнопку ниже, чтобы открыть приложение:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

async def setup_webhook():
    if bot and RENDER_URL:
        webhook_url = f"{RENDER_URL}/webhook/{TOKEN}"
        await bot.set_webhook(webhook_url)
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🏃 Zer0Life Run",
                web_app=WebAppInfo(url="https://wlodek2106-cyber.github.io/Zer0life_ai/")
            )
        )
        logging.info(f"Вебхук успешно установлен на: {webhook_url}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    if TOKEN:
        asyncio.run(setup_webhook())
    
    port = int(os.environ.get("PORT", 8080))
    api_app.run(host="0.0.0.0", port=port)
