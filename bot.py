import json
import logging
import sys
import os
import threading
import asyncio
import base58
import traceback
import random
import datetime
import aiohttp

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
from solders.signature import Signature
from solders.system_program import transfer, TransferParams
from spl.token.instructions import transfer_checked, TransferCheckedParams, get_associated_token_address, create_associated_token_account

# Добавлено для планировщика марафона
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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

# ==================== СИСТЕМА МАССОВЫХ РАССЫЛОК И КУРСОВ PHANTOM / JUPITER ====================
async def send_broadcast_to_all(text: str):
    users = load_users()
    if not bot:
        return
    for user_id in users:
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Open Zer0Life Run & Casino", web_app=WebAppInfo(url="https://wlodek2106-cyber.github.io/Zer0life_ai/"))]
            ])
            await bot.send_message(chat_id=int(user_id), text=text, reply_markup=keyboard, parse_mode="HTML")
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.error(f"Failed to send broadcast to {user_id}: {e}")

@api_app.route('/broadcast', methods=['POST'])
def api_broadcast():
    try:
        data = request.json
        if not data or 'text' not in data:
            return jsonify({"success": False, "error": "Missing text parameter"}), 400
        
        message_text = data['text']
        asyncio.run_coroutine_threadsafe(send_broadcast_to_all(message_text), bot_loop)
        return jsonify({"success": True, "recipients": get_total_users()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

# Автоматический генератор игровых событий
async def automated_ecosystem_notifications():
    while True:
        await asyncio.sleep(1800)
        if get_total_users() == 0:
            continue
            
        prompts = [
            "📤 <b>Withdrawal Alert:</b> A player successfully withdrew 5,000 ZRL to their Solana wallet!",
            "🎰 <b>Casino Alert:</b> CryptoNinja just hit a massive multiplier and won 10,000 ZRL in Slots!",
            "🌾 <b>Farming Alert:</b> A user locked 5 SOL in the 300% APY Super Yield farming pool!",
            "🌾 <b>ZRL Farming Notice:</b> Someone just staked 250,000 ZRL into the 12-month farming vault!",
            "👟 <b>Store Alert:</b> A runner just purchased <b>Boots x2</b> (7-day boost) from the merchant store!",
            "⚡ <b>VIP Store Alert:</b> A whale player just unlocked the <b>Boots x10</b> speed package!",
            "🔥 <b>Jackpot Alert:</b> Another lucky runner won Solana in the Zer0Life Casino roulette!"
        ]
        chosen_text = random.choice(prompts)
        await send_broadcast_to_all(chosen_text)

# Сводка цен BTC, ETH, SOL и ZRL через оракулы Solana / Phantom (Jupiter Price API)
async def crypto_market_price_alerts():
    while True:
        await asyncio.sleep(10800) # Каждые 3 часа
        if get_total_users() == 0:
            continue
        
        try:
            async with aiohttp.ClientSession() as session:
                ids = "SOL,BTC,ETH"
                zrl_mint = os.getenv('ZRL_MINT', '')
                if zrl_mint:
                    ids += f",{zrl_mint}"

                jup_url = f"https://price.jup.ag/v6/price?ids={ids}"
                async with session.get(jup_url) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        data = res.get('data', {})
                        
                        date_str = datetime.datetime.now().strftime("%B %d, %Y")
                        msg = f"📢 <b>Phantom Market Overview — {date_str}</b>\n\n"
                        
                        sol_p = data.get('SOL', {}).get('price', 0)
                        btc_p = data.get('BTC', {}).get('price', 0)
                        eth_p = data.get('ETH', {}).get('price', 0)
                        
                        msg += f"<b>SOL:</b> ${sol_p:,.2f} (Phantom Oracle 🟢)\n"
                        msg += f"<b>BTC:</b> ${btc_p:,.2f} (Phantom Oracle 🟢)\n"
                        msg += f"<b>ETH:</b> ${eth_p:,.2f} (Phantom Oracle 🟢)\n"

                        if zrl_mint and zrl_mint in data:
                            zrl_p = data[zrl_mint].get('price', 0)
                            msg += f"<b>ZRL Token:</b> ${zrl_p:.4f} (DEX Live 🚀)\n"
                        else:
                            msg += "<b>ZRL Token:</b> Not Listed Yet (Keep Farming! 🌾)\n"
                        
                        await send_broadcast_to_all(msg)
        except Exception as e:
            logging.error(f"Phantom price fetch error: {e}")

# Планировщик для старта марафона (7 октября 2026 года в 00:00 UTC)
marathon_scheduler = AsyncIOScheduler(timezone="UTC")

async def send_marathon_start_push():
    text = (
        "🚨 <b>MARATHON STARTED!</b> 🚨\n\n"
        "The 30-day ZRL marathon has officially begun! 🏆\n"
        "All kilometers have been reset, and the 1,000,000 ZRL prize pool is up for grabs. "
        "Lace up your sneakers and start running right now! 🚀"
    )
    await send_broadcast_to_all(text)

def schedule_marathon_start():
    marathon_time = datetime.datetime(2026, 10, 7, 0, 0, 0)
    marathon_scheduler.add_job(
        send_marathon_start_push,
        'date',
        run_date=marathon_time,
        id='marathon_start_event'
    )
    marathon_scheduler.start()

def start_periodic_notifications():
    asyncio.run_coroutine_threadsafe(automated_ecosystem_notifications(), bot_loop)
    asyncio.run_coroutine_threadsafe(crypto_market_price_alerts(), bot_loop)
    schedule_marathon_start() # Запуск планировщика марафона в фоновом потоке бота
# ==============================================================================

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

@api_app.route('/verify-deposit', methods=['POST'])
def verify_deposit():
    try:
        data = request.json
        if not data or 'txSignature' not in data:
            return jsonify({"success": False, "error": "Missing transaction signature"}), 400

        tx_signature_str = data['txSignature'].strip()
        client = Client("https://api.mainnet-beta.solana.com")

        sig_obj = Signature.from_string(tx_signature_str)

        tx_status = client.get_signature_statuses([sig_obj])
        if not tx_status or not tx_status.value or not tx_status.value[0]:
            return jsonify({"success": False, "error": "Транзакция не найдена в блокчейне"}), 400

        status_info = tx_status.value[0]
        if status_info.err is not None:
            return jsonify({"success": False, "error": "Транзакция завершилась с ошибкой в блокчейне"}), 400

        deposited_sol = 0.0
        try:
            tx_details = client.get_transaction(
                sig_obj, 
                max_supported_transaction_version=0
            )
            if tx_details and tx_details.value:
                meta = tx_details.value.transaction.meta
                if meta and meta.pre_balances and meta.post_balances:
                    for i in range(len(meta.post_balances)):
                        d = (meta.post_balances[i] - meta.pre_balances[i]) / (10**9)
                        if d > deposited_sol:
                            deposited_sol = d
        except Exception as ex:
            print("Notice parsing tx details:", ex)

        if deposited_sol <= 0:
            deposited_sol = 0.001

        return jsonify({
            "success": True,
            "depositedSOL": round(deposited_sol, 4)
        })
    except Exception as e:
        print("ERROR IN VERIFY DEPOSIT:", traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 400

@api_app.route('/withdraw', methods=['POST'])
def withdraw():
    try:
        data = request.json
        if not data or 'walletAddress' not in data or 'amount' not in data:
            return jsonify({"error": "Invalid request data"}), 400

        user_wallet_str = data['walletAddress']
        amount = float(data['amount'])
        currency = data.get('currency', 'ZRL').upper()
        tg_id = data.get('telegramId')
        
        if tg_id:
            save_user(tg_id)

        private_key_base58 = os.getenv('PRIVATE_KEY')
        if not private_key_base58:
            return jsonify({"error": "Server config error: missing PRIVATE_KEY"}), 500
            
        pool_keypair = Keypair.from_bytes(base58.b58decode(private_key_base58))
        user_pubkey = Pubkey.from_string(user_wallet_str)
        client = Client("https://api.mainnet-beta.solana.com")
        
        instructions = []
        
        if currency == 'SOL':
            lamports = int(amount * (10**9))
            transfer_ix = transfer(
                TransferParams(
                    from_pubkey=pool_keypair.pubkey(),
                    to_pubkey=user_pubkey,
                    lamports=lamports
                )
            )
            instructions.append(transfer_ix)
        else:
            zrl_mint_address = os.getenv('ZRL_MINT')
            if not zrl_mint_address:
                return jsonify({"error": "Server config error: missing ZRL_MINT"}), 500
                
            zrl_mint = Pubkey.from_string(zrl_mint_address)
            pool_ata = get_associated_token_address(pool_keypair.pubkey(), zrl_mint)
            user_ata = get_associated_token_address(user_pubkey, zrl_mint)
            
            try:
                account_info = client.get_account_info(user_ata)
                if account_info.value is None:
                    instructions.append(
                        create_associated_token_account(
                            payer=pool_keypair.pubkey(),
                            owner=user_pubkey,
                            mint=zrl_mint
                        )
                    )
            except Exception as e:
                print("ATA check/creation notice:", e)

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
            instructions.append(transfer_ix)
        
        recent_blockhash_resp = client.get_latest_blockhash()
        recent_blockhash = recent_blockhash_resp.value.blockhash
        
        tx = Transaction.new_signed_with_payer(
            instructions=instructions,
            payer=pool_keypair.pubkey(),
            signing_keypairs=[pool_keypair],
            recent_blockhash=recent_blockhash
        )
        
        result = client.send_raw_transaction(bytes(tx))
        tx_signature = str(result.value)
        
        if tg_id and bot:
            msg_text = (
                f"✅ Вывод успешно завершен!\n\n"
                f"Сумма: {amount} {currency}\n"
                f"Кошелек: {user_wallet_str[:6]}...{user_wallet_str[-4:]}\n\n"
                f"🔗 Просмотреть транзакцию на Solscan\n"
                f"https://solscan.io/tx/{tx_signature}"
            )
            asyncio.run_coroutine_threadsafe(
                bot.send_message(
                    chat_id=int(tg_id),
                    text=msg_text,
                    disable_web_page_preview=True
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
        start_periodic_notifications()
    
    port = int(os.environ.get("PORT", 10000))
    api_app.run(host="0.0.0.0", port=port)
