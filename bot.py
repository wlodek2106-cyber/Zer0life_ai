import json
import logging
import sys
import os
import threading
import base58
import base64

from aiogram import Bot, Dispatcher, F, types
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
        
        # 1. Создаем сообщение транзакции с указанием плательщика комиссии (user_pubkey)
        message = Message.new_with_payer(
            [transfer_ix],
            user_pubkey
        )
        
        # 2. Инициализируем транзакцию через классический конструктор solders
        tx = Transaction(
            [pool_keypair], 
            message, 
            recent_blockhash
        )
        
        serialized_tx = base64.b64encode(tx.serialize()).decode('utf-8')
        return jsonify({"transaction": serialized_tx})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 400

def run_api_server():
    port = int(os.environ.get("PORT", 8080))
    api_app.run(host="0.0.0.0", port=port)

TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()

async def set_main_menu(bot: Bot):
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🏃 Zer0Life Run",
            web_app=WebAppInfo(url="https://wlodek2106-cyber.github.io/Zer0life_ai/")
        )
    )

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏃 Запустить Zer0Life Run", web_app=WebAppInfo(url="https://wlodek2106-cyber.github.io/Zer0life_ai/"))]
    ])
    await message.answer(
        "Йоу! Добро пожаловать в <b>Zer0Life</b> ⚡️\n\n"
        "Шагай, тренируйся и зарабатывай <b>ZRL</b>!\n"
        "Нажми кнопку ниже, чтобы начать:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.message(Command("run"))
async def cmd_run(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏃 Запустить Zer0Life Run", web_app=WebAppInfo(url="https://wlodek2106-cyber.github.io/Zer0life_ai/"))]
    ])
    await message.answer("Откройте мини-приложение:", reply_markup=keyboard)

@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        distance = data.get("distance", 0)
        reward = data.get("reward", 0)
        
        await message.answer(
            f"Отличная тренировка! 🏁\n"
            f"Дистанция: {distance} км\n"
            f"Начислено на баланс: <b>{reward} ZRL</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer("Не удалось обработать результаты тренировки.")

async def main():
    if not TOKEN:
        logging.error("ОШИБКА: Переменная BOT_TOKEN не найдена на сервере!")
        return

    threading.Thread(target=run_api_server, daemon=True).start()
    
    bot = Bot(token=TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    await set_main_menu(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    import asyncio
    asyncio.run(main())
