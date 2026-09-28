import json
import logging
import sys
import os
import threading
import asyncio
import base58
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

# ==================== НАСТРОЙКИ АДМИНА ====================
ADMIN_TELEGRAM_ID = "428821665"
# Жестко прописан твой системный адрес пула для отображения и депо
ADMIN_POOL_WALLET = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix" 
# ==========================================================

dp = Dispatcher()
bot = Bot(token=TOKEN) if TOKEN else None

USERS_FILE = "users.json"
STATS_FILE = "leaderboard_stats.json"

def get_pool_pubkey_str():
    try:
        pk = os.getenv('PRIVATE_KEY')
        if pk:
            kp = Keypair.from_bytes(base58.b58decode(pk))
            return str(kp.pubkey())
    except:
        pass
    return ADMIN_POOL_WALLET

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                return set(json.load(f))
        except:
            pass
    return set()

def save_user(user_id):
    users = load_users()
    users.add(str(user_id))
    try:
        with open(USERS_FILE, "w") as f:
            json.dump(list(users), f)
    except Exception as e:
        logging.error(f"Error saving user: {e}")

def get_total_users():
    return len(load_users())

def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_stats_data(stats):
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f)
    except Exception as e:
        logging.error(f"Error saving stats: {e}")

bot_loop = asyncio.new_event_loop()
def start_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=start_background_loop, args=(bot_loop,), daemon=True).start()

@api_app.route('/update-stats', methods=['POST'])
def update_stats():
    try:
        data = request.json
        if not data or 'telegramId' not in data:
            return jsonify({"error": "Invalid data"}), 400
        
        tg_id = str(data['telegramId'])
        stats = load_stats()
        
        stats[tg_id] = {
            "name": data.get('username', 'Runner'),
            "wallet": data.get('walletAddress', ''),
            "distance": float(data.get('distance', 0.0)),
            "balance": float(data.get('balance', 0.0))
        }
        save_stats_data(stats)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@api_app.route('/leaderboard', methods=['GET'])
def get_leaderboard():
    try:
        stats = load_stats()
        sorted_users = sorted(stats.values(), key=lambda x: x['distance'], reverse=True)
        
        leaderboard = []
        for index, user in enumerate(sorted_users[:50], start=1):
            dist = float(user.get('distance', 0.0))
            level = int(dist // 5) + 1
            
            leaderboard.append({
                "rank": index,
                "name": f"{user.get('name', 'Runner')} (Lvl {level})",
                "dist": round(dist, 2),
                "zrl": int(user.get('balance', 0.0))
            })
            
        return jsonify({
            "success": True,
            "leaderboard": leaderboard,
            "myRank": {"rank": 1}
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@api_app.route('/check-deposit', methods=['POST'])
def check_deposit():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Invalid request data"}), 400

        tg_id = str(data.get('telegramId', ''))
        
        # Если заходит админ, проверяем системный пул
        if tg_id == ADMIN_TELEGRAM_ID:
            wallet_str = get_pool_pubkey_str()
        else:
            wallet_str = data.get('walletAddress')

        if not wallet_str:
            return jsonify({"error": "Wallet address missing"}), 400

        client = Client("https://api.mainnet-beta.solana.com")
        pubkey = Pubkey.from_string(wallet_str)
        
        sol_response = client.get_balance(pubkey)
        sol_balance = sol_response.value / (10**9) if sol_response.value else 0.0
        
        usdc_balance = 0.0
        try:
            usdc_mint = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
            usdc_ata = get_associated_token_address(pubkey, usdc_mint)
            token_account_resp = client.get_token_account_balance(usdc_ata)
            if token_account_resp and token_account_resp.value:
                usdc_balance = float(token_account_resp.value.ui_amount or 0.0)
        except Exception as e:
            print("USDC balance fetch notice:", e)

        zrl_balance = 0.0
        zrl_mint_address = os.getenv('ZRL_MINT')
        if zrl_mint_address:
            try:
                zrl_mint = Pubkey.from_string(zrl_mint_address)
                zrl_ata = get_associated_token_address(pubkey, zrl_mint)
                zrl_resp = client.get_token_account_balance(zrl_ata)
                if zrl_resp and zrl_resp.value:
                    zrl_balance = float(zrl_resp.value.ui_amount or 0.0)
            except Exception as e:
                print("ZRL balance fetch notice:", e)

        return jsonify({
            "success": True, 
            "solBalance": sol_balance,
            "usdcBalance": usdc_balance,
            "zrlBalance": zrl_balance
        })
        
    except Exception as e:
        print("ERROR IN DEPOSIT CHECK:", traceback.format_exc())
        return jsonify({"error": str(e)}), 400

@api_app.route('/withdraw', methods=['POST'])
def withdraw():
    try:
        zrl_mint_address = os.getenv('ZRL_MINT')
        private_key_base58 = os.getenv('PRIVATE_KEY')
        
        missing = []
        if not zrl_mint_address: missing.append('ZRL_MINT')
        if not private_key_base58: missing.append('PRIVATE_KEY')
        
        if missing:
            return jsonify({"error": f"Server config error: missing env vars: {', '.join(missing)}"}), 500

        data = request.json
        if not data or 'walletAddress' not in data or 'amount' not in data:
            return jsonify({"error": "Invalid request data"}), 400

        user_wallet_str = data['walletAddress']
        amount = float(data['amount'])
        tg_id = data.get('telegramId')
        
        if tg_id:
            save_user(tg_id)

        user_pubkey = Pubkey.from_string(user_wallet_str)
        zrl_mint = Pubkey.from_string(zrl_mint_address)
        pool_keypair = Keypair.from_bytes(base58.b58decode(private_key_base58))
        
        pool_ata = get_associated_token_address(pool_keypair.pubkey(), zrl_mint)
        user_ata = get_associated_token_address(user_pubkey, zrl_mint)
        
        client = Client("https://api.mainnet-beta.solana.com")
        
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
        
        recent_blockhash_str = client.get_latest_blockhash().value.blockhash
        recent_blockhash = Hash.from_string(str(recent_blockhash_str))
        
        tx = Transaction.new_signed_with_payer(
            instructions=[transfer_ix],
            payer=pool_keypair.pubkey(),
            signing_keypairs=[pool_keypair],
            recent_blockhash=recent_blockhash
        )
        
        result = client.send_transaction(tx)
        tx_signature = str(result.value)
        
        if tg_id and bot:
            asyncio.run_coroutine_threadsafe(
                bot.send_message(
                    chat_id=int(tg_id),
                    text=f"✅ **Withdrawal Successful!**\n\n"
                         f"Amount: `{amount} ZRL`\n"
                         f"Wallet: `{user_wallet_str[:6]}...{user_wallet_str[-4:]}`\n\n"
                         f"🔗 [View transaction on Solscan](https://solscan.io/tx/{tx_signature})",
                    parse_mode="Markdown"
                ),
                bot_loop
            )

        return jsonify({"success": True, "txHash": tx_signature})
        
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
    save_user(message.from_user.id)
    total_users = get_total_users()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏃 Launch Zer0Life Run", web_app=WebAppInfo(url="https://wlodek2106-cyber.github.io/Zer0life_ai/"))]
    ])
    await message.answer(
        f"Yo! Welcome to <b>Zer0Life Run</b> ⚡️\n\n"
        f"👥 Total Runners: <b>{total_users}</b>\n"
        f"Train, walk and earn <b>ZRL</b> tokens!\n\n"
        f"Click the button below to open the app:",
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
            logging.info(f"Webhook registered: {webhook_url}")
        except Exception as e:
            logging.error(f"Webhook setup error: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    if TOKEN:
        setup_webhook_sync()
    
    port = int(os.environ.get("PORT", 10000))
    api_app.run(host="0.0.0.0", port=port)
