import json
import logging
import sys
import os
import threading
import asyncio
import base58
import base64
import traceback

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
CORS(api_app, resources={r"/*": {"origins": "*"}})

@api_app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        response = jsonify({"status": "ok"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "GET,PUT,POST,DELETE,OPTIONS")
        return response, 200

@api_app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    return response

TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = "https://zer0life-ai-iz5n.onrender.com"

dp = Dispatcher()
bot = Bot(token=TOKEN) if TOKEN else None

bot_loop = asyncio.new_event_loop()
def start_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=start_background_loop, args=(bot_loop,), daemon=True).start()

@api_app.route('/withdraw', methods=['POST'])
def withdraw():
    try:
        data = request.json
        if not data or 'walletAddress' not in data or 'amount' not in data:
            return jsonify({"error": "Неверные данные запроса"}), 400

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
        
        # Компилируем сообщение через Message с указанием плательщика комиссии (пользователь)
        message = Message.new_with_blockhash(
            instructions=[transfer_ix],
            payer=user_pubkey,
            blockhash=recent_blockhash
        )
        
        # Создаем транзакцию с подписью пула
        tx = Transaction.new_unsigned(message)
        tx.sign([pool_keypair], recent_blockhash)
        
        serialized_tx = base64.b64encode(tx.serialize()).decode('utf-8')
        return jsonify({"transaction": serialized_tx})
        
    except Exception as e:
        error_trace = traceback.format_exc()
        print("ERROR IN WITHDRAW:", error_trace)
        return jsonify({"error": str(e)}), 400

@api_app.route(f'/webhook/{TOKEN}', methods=['POST'])
def telegram_webhook():
    if request.headers.get('content-type') == 'application/json':
        json_data = request.get_json()
        try:
            update = types.Update.model_validate(json_data, context={"bot": bot})
            asyncio.run_coroutine_threadsafe(dp.feed_update(bot, update), bot_loop)
        except Exception as e:
            logging.error(f"Webhook error: {e}")
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

def setup_webhook_sync():
    if bot and RENDER_URL:
        try:
            webhook_url = f"{RENDER_URL}/webhook/{TOKEN}"
            future = asyncio.run_coroutine_threadsafe(bot.set_webhook(webhook_url), bot_loop)
            future.result(timeout=5)
            
            menu_future = asyncio.run_coroutine_threadsafe(bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🏃 Zer0Life Run",
                    web_app=WebAppInfo(url="https://wlodek2106-cyber.github.io/Zer0life_ai/")
                )
            ), bot_loop)
            menu_future.result(timeout=5)
            logging.info(f"Вебхук зарегистрирован: {webhook_url}")
        except Exception as e:
            logging.error(f"Webhook setup error: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    if TOKEN:
        setup_webhook_sync()
    port = int(os.environ.get("PORT", 8080))
    api_app.run(host="0.0.0.0", port=port)
