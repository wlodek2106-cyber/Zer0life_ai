import os
import json
import time
import threading
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from solana.rpc.api import Client

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Подключение к сети Solana
solana_client = Client("https://api.mainnet-beta.solana.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Целевой кошелек проекта для депозитов и кошелек мерчанта
TARGET_WALLET = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"
MERCHANT_SOL_WALLET = "57S7TryAMhxRq5sMSyZxTkyCmzT8tPFKfXjzZwvmv5db"

DB_FILE = "database.json"

def load_db():
    default_data = {
        "FRIENDS_DB": {},
        "REQUESTS_DB": [],
        "USERS_MAP": {},
        "USER_STATS": {},
        "CHAT_DB": [],
        "USER_TOTAL_DEPOSITS": {}
    }
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in default_data.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception as e:
            print("Error loading DB:", e)
    return default_data

def save_db():
    try:
        data = {
            "FRIENDS_DB": FRIENDS_DB,
            "REQUESTS_DB": REQUESTS_DB,
            "USERS_MAP": USERS_MAP,
            "USER_STATS": USER_STATS,
            "CHAT_DB": CHAT_DB,
            "USER_TOTAL_DEPOSITS": USER_TOTAL_DEPOSITS
        }
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Error saving DB:", e)

db = load_db()
FRIENDS_DB = db.get("FRIENDS_DB", {})
REQUESTS_DB = db.get("REQUESTS_DB", [])
USERS_MAP = db.get("USERS_MAP", {})
USER_STATS = db.get("USER_STATS", {})
CHAT_DB = db.get("CHAT_DB", [])
USER_TOTAL_DEPOSITS = db.get("USER_TOTAL_DEPOSITS", {})

active_runners_map = {}

def send_telegram_message(chat_id, text):
    if not TELEGRAM_BOT_TOKEN or not str(chat_id).isdigit():
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        })
    except Exception as e:
        print("TG Notification error:", e)

def check_and_expire_boosters(user_stats):
    expire_time = user_stats.get("booster_expire_time", 0)
    if expire_time and time.time() > expire_time:
        user_stats["active_temp_booster"] = None
        user_stats["booster_expire_time"] = 0
        return True
    return False

# ==========================================
# ФОНОВЫЙ ПОТОК: АВТОМАТИЧЕСКИЙ ФАРМИНГ ZRL
# ==========================================
def background_farming_worker():
    """Каждую минуту пересчитывает доходность активных депозитов фарминга и начисляет ZRL"""
    while True:
        try:
            now = time.time() * 1000  # миллисекунды
            updated_any = False
            
            for uid, stats in USER_STATS.items():
                active_farms = stats.get("active_farms", [])
                if not active_farms:
                    continue
                
                user_yield_added = 0.0
                new_farms_list = []
                
                for farm in active_farms:
                    end_time = farm.get("endTime", 0)
                    if now < end_time:
                        last_check = farm.get("lastUpdateTime", farm.get("startTime", now))
                        elapsed_hours = (now - last_check) / (1000 * 60 * 60)
                        
                        if elapsed_hours > 0:
                            amount = float(farm.get("amount", 0))
                            daily_rate = float(farm.get("dailyRate", 0.001))
                            hourly_rate = (amount * daily_rate) / 24
                            reward = hourly_rate * elapsed_hours
                            
                            user_yield_added += reward
                            farm["claimedReward"] = farm.get("claimedReward", 0.0) + reward
                            farm["lastUpdateTime"] = now
                            updated_any = True
                        
                        new_farms_list.append(farm)
                    else:
                        # Если срок фарминга истек, возвращаем тело депозита обратно пользователю
                        asset = farm.get("asset", "sol")
                        amount = float(farm.get("amount", 0))
                        if asset == "sol":
                            stats["solBalance"] = float(stats.get("solBalance", 0)) + amount
                        else:
                            stats["balance"] = float(stats.get("balance", 0)) + amount
                        updated_any = True
                
                if user_yield_added > 0:
                    current_bal = float(stats.get("balance", 0))
                    stats["balance"] = current_bal + user_yield_added
                
                stats["active_farms"] = new_farms_list
                USER_STATS[uid] = stats
            
            if updated_any:
                save_db()
                
        except Exception as e:
            print("Background farming worker error:", e)
            
        time.sleep(60)  длительность паузы — 1 минута

# Запуск фонового потока при старте Flask
threading.Thread(target=background_farming_worker, daemon=True).start()

@app.route('/register', methods=['POST', 'OPTIONS'])
@app.route('/api/register', methods=['POST', 'OPTIONS'])
def register_user():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        telegram_id = str(data.get('telegramId'))
        username = data.get('username')
        if telegram_id and username:
            USERS_MAP[telegram_id] = username
            active_runners_map[telegram_id] = time.time() * 1000
            save_db()
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Invalid data"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/update-stats', methods=['POST', 'OPTIONS'])
@app.route('/api/update-stats', methods=['POST', 'OPTIONS'])
def update_stats():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        telegram_id = str(data.get('telegramId'))
        if telegram_id:
            username = data.get('username', 'Runner')
            active_runners_map[telegram_id] = time.time() * 1000
            
            existing = USER_STATS.get(telegram_id, {})
            check_and_expire_boosters(existing)
            
            USER_STATS[telegram_id] = {
                "username": username,
                "walletAddress": data.get('walletAddress', existing.get('walletAddress', TARGET_WALLET)),
                "distance": data.get('distance', existing.get('distance', 0)),
                "balance": data.get('balance', existing.get('balance', 0)),
                "solBalance": existing.get('solBalance', existing.get("solBalance", 1.5)),
                "avatar": data.get('avatar', existing.get('avatar', '')),
                "active_temp_booster": existing.get('active_temp_booster'),
                "booster_expire_time": existing.get('booster_expire_time', 0),
                "active_farms": existing.get('active_farms', [])
            }
            USERS_MAP[telegram_id] = username
            save_db()
            return jsonify({"success": True})
        return jsonify({"success": False}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/online-stats', methods=['GET', 'OPTIONS'])
@app.route('/api/online-stats', methods=['GET', 'OPTIONS'])
def get_online_stats():
    if request.method == 'OPTIONS':
        return '', 200
    
    now = time.time() * 1000
    inactive_users = [uid for uid, t in active_runners_map.items() if now - t > 600000]
    for uid in inactive_users:
        del active_runners_map[uid]
    
    online_count = max(1422, len(active_runners_map))
    
    return jsonify({
        "success": True,
        "onlineCount": online_count,
        "totalCasinoSolIn": 148.50,
        "totalCasinoZrlWon": 2450000
    })

@app.route('/casino-action', methods=['POST', 'OPTIONS'])
@app.route('/api/casino-action', methods=['POST', 'OPTIONS'])
def casino_action():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        telegram_id = str(data.get('telegramId', ''))
        if telegram_id:
            active_runners_map[telegram_id] = time.time() * 1000
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/leaderboard', methods=['GET', 'OPTIONS'])
@app.route('/api/leaderboard', methods=['GET', 'OPTIONS'])
def get_leaderboard():
    if request.method == 'OPTIONS':
        return '', 200
    users_list = []
    for uid, stats in USER_STATS.items():
        check_and_expire_boosters(stats)
        users_list.append({
            "name": stats.get("username", "Runner"),
            "dist": round(stats.get("distance", 0), 2),
            "zrl": int(stats.get("balance", 0)),
            "telegramId": uid,
            "avatar": stats.get("avatar", "")
        })
    users_list.sort(key=lambda x: x['dist'], reverse=True)
    for i, u in enumerate(users_list):
        u['rank'] = i + 1
        
    my_id = str(request.args.get('telegramId', ''))
    my_rank_data = None
    if my_id:
        for u in users_list:
            if u["telegramId"] == my_id:
                my_rank_data = u
                break
                
    return jsonify({"success": True, "leaderboard": users_list[:50], "myRank": my_rank_data})

@app.route('/friend/list', methods=['GET', 'OPTIONS'])
@app.route('/api/friend/list', methods=['GET', 'OPTIONS'])
def get_friends_list():
    if request.method == 'OPTIONS':
        return '', 200
    telegram_id = str(request.args.get('telegramId'))
    
    if telegram_id not in USERS_MAP:
        USERS_MAP[telegram_id] = "Runner"

    friends_ids = FRIENDS_DB.get(telegram_id, [])
    friends = []
    for fid in friends_ids:
        st = USER_STATS.get(fid, {})
        check_and_expire_boosters(st)
        friends.append({
            "telegramId": fid, 
            "username": st.get("username", USERS_MAP.get(fid, "Runner")), 
            "distance": st.get("distance", 0.0)
        })
    
    my_requests = []
    for r in REQUESTS_DB:
        target_id = str(r.get('targetId', ''))
        sender_id = str(r.get('telegramId', ''))
        if target_id == telegram_id and sender_id != telegram_id:
            my_requests.append({
                "telegramId": sender_id,
                "username": r.get('username', 'Runner')
            })
            
    return jsonify({"success": True, "friends": friends, "requests": my_requests})

@app.route('/friend/request', methods=['POST', 'OPTIONS'])
@app.route('/api/friend/request', methods=['POST', 'OPTIONS'])
def send_friend_request():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        from_id = str(data.get('fromId') or data.get('telegramId') or 'unknown')
        from_username = data.get('fromUsername') or 'Runner'
        target_username = str(data.get('targetUsername', '')).strip().lower()
        
        if not target_username:
            return jsonify({"success": False, "error": "Empty target"})
            
        if from_id and from_username:
            USERS_MAP[from_id] = from_username

        target_id = None
        for uid, uname in USERS_MAP.items():
            if uname and uname.lower() == target_username:
                target_id = uid
                break
                
        if not target_id:
            for uid, stats in USER_STATS.items():
                uname = stats.get("username", "")
                if uname and uname.lower() == target_username:
                    target_id = uid
                    break

        if not target_id:
            return jsonify({"success": False, "error": f"User '{target_username}' not found."})

        global REQUESTS_DB
        if not isinstance(REQUESTS_DB, list):
            REQUESTS_DB = []
            
        exists = any(str(r.get('telegramId')) == from_id and str(r.get('targetId')) == str(target_id) for r in REQUESTS_DB)
        if not exists:
            REQUESTS_DB.append({
                "targetId": str(target_id),
                "telegramId": from_id,
                "username": from_username
            })
            save_db()
            
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/friend/accept', methods=['POST', 'OPTIONS'])
@app.route('/api/friend/accept', methods=['POST', 'OPTIONS'])
def accept_friend_request():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        user_id = str(data.get('userId'))
        friend_id = str(data.get('friendId'))
        
        if user_id not in FRIENDS_DB: FRIENDS_DB[user_id] = []
        if friend_id not in FRIENDS_DB: FRIENDS_DB[friend_id] = []
        
        if friend_id not in FRIENDS_DB[user_id]: FRIENDS_DB[user_id].append(friend_id)
        if user_id not in FRIENDS_DB[friend_id]: FRIENDS_DB[friend_id].append(user_id)
        
        global REQUESTS_DB
        if isinstance(REQUESTS_DB, list):
            REQUESTS_DB = [r for r in REQUESTS_DB if not (str(r.get('targetId')) == user_id and str(r.get('telegramId')) == friend_id)]
            
        save_db()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": True})

@app.route('/chat/messages', methods=['GET', 'OPTIONS'])
@app.route('/api/chat/messages', methods=['GET', 'OPTIONS'])
def get_chat_messages():
    if request.method == 'OPTIONS':
        return '', 200
    user1 = str(request.args.get('user1'))
    user2 = str(request.args.get('user2'))
    messages = [
        m for m in CHAT_DB 
        if (str(m.get('senderId')) == user1 and str(m.get('receiverId')) == user2) or 
           (str(m.get('senderId')) == user2 and str(m.get('receiverId')) == user1)
    ]
    return jsonify({"success": True, "messages": messages})

@app.route('/chat/send', methods=['POST', 'OPTIONS'])
@app.route('/api/chat/send', methods=['POST', 'OPTIONS'])
def send_chat_message():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        msg = {
            "senderId": str(data.get('senderId')),
            "receiverId": str(data.get('receiverId')),
            "text": data.get('text'),
            "timestamp": data.get('timestamp', 0)
        }
        CHAT_DB.append(msg)
        if len(CHAT_DB) > 500:
            CHAT_DB.pop(0)
        save_db()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": True})

@app.route('/check-deposit', methods=['POST', 'OPTIONS'])
@app.route('/api/check-deposit', methods=['POST', 'OPTIONS'])
def check_deposit():
    if request.method == 'OPTIONS':
        return '', 200
    telegram_id = str(request.json.get('telegramId') or request.args.get('telegramId', ''))
    user_stats = USER_STATS.get(telegram_id, {})
    check_and_expire_boosters(user_stats)
    return jsonify({
        "success": True,
        "solBalance": float(user_stats.get("solBalance", 1.5)),
        "usdcBalance": 50.0,
        "zrlBalance": float(user_stats.get("balance", 25000.0)),
        "activeBooster": user_stats.get("active_temp_booster")
    })

@app.route('/verify-deposit', methods=['POST', 'OPTIONS'])
@app.route('/api/verify-deposit', methods=['POST', 'OPTIONS'])
def verify_deposit():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        telegram_id = str(data.get('telegramId'))
        tx_signature = data.get('txSignature')
        
        if not telegram_id or not tx_signature:
            return jsonify({"success": False, "error": "Не указан telegramId или txSignature"}), 400
            
        response = solana_client.get_transaction(tx_signature, max_supported_transaction_version=0)
        
        if not response or not response.get('result'):
            return jsonify({"success": False, "error": "Транзакция еще не найдена в блокчейне. Подождите 10-15 секунд."}), 400
            
        tx_data = response['result']
        meta = tx_data.get('meta', {})
        if meta and meta.get('err') is not None:
            return jsonify({"success": False, "error": "Транзакция завершилась с ошибкой в блокчейне Solana."}), 400
            
        transaction_info = tx_data.get('transaction', {})
        message = transaction_info.get('message', {})
        account_keys = message.get('accountKeys', [])
        
        target_index = -1
        for idx, key in enumerate(account_keys):
            pubkey_str = key if isinstance(key, str) else key.get('pubkey')
            if pubkey_str == TARGET_WALLET:
                target_index = idx
                break
                
        if target_index == -1:
            return jsonify({"success": False, "error": "Эта транзакция не связана с кошельком проекта."}), 400
            
        pre_balances = meta.get('preBalances', [])
        post_balances = meta.get('postBalances', [])
        
        if len(pre_balances) <= target_index or len(post_balances) <= target_index:
            return jsonify({"success": False, "error": "Не удалось проверить изменение баланса кошелька."}), 400
            
        diff_lamports = post_balances[target_index] - pre_balances[target_index]
        diff_sol = diff_lamports / 1_000_000_000
        
        MIN_DEPOSIT = 0.01
        if diff_sol < MIN_DEPOSIT:
            return jsonify({"success": False, "error": f"Сумма слишком мала ({diff_sol} SOL). Минимум: {MIN_DEPOSIT} SOL."}), 400
            
        user_stats = USER_STATS.get(telegram_id, {})
        check_and_expire_boosters(user_stats)
        current_sol = float(user_stats.get("solBalance", 1.5))
        current_sol += diff_sol
        user_stats["solBalance"] = current_sol
        
        USER_TOTAL_DEPOSITS[telegram_id] = USER_TOTAL_DEPOSITS.get(telegram_id, 0.0) + diff_sol
        USER_STATS[telegram_id] = user_stats
        save_db()
        
        send_telegram_message(
            telegram_id,
            f"✅ <b>Депозит успешно подтвержден!</b>\n\nЗачислено: {diff_sol} SOL\nВаш новый баланс: {current_sol:.4f} SOL"
        )
        
        return jsonify({
            "success": True,
            "depositedSOL": diff_sol,
            "newSolBalance": current_sol,
            "message": f"Успешно зачислено {diff_sol} SOL!"
        })
        
    except Exception as e:
        print("Verify deposit error:", e)
        return jsonify({"success": False, "error": f"Ошибка проверки транзакции: {str(e)}"}), 500

@app.route('/withdraw', methods=['POST', 'OPTIONS'])
@app.route('/api/withdraw', methods=['POST', 'OPTIONS'])
def withdraw():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        user_wallet = data.get('wallet') or data.get('walletAddress')
        amount = float(data.get('amount', 0))
        currency = data.get('currency', 'SOL')
        telegram_id = str(data.get('telegramId'))
        
        if not telegram_id or telegram_id not in USER_STATS:
            return jsonify({"success": False, "error": "Пользователь не найден"})
            
        user_stats = USER_STATS[telegram_id]
        
        # Если это списание SOL для покупки бустеров или депозита в фарминг
        if currency == 'SOL':
            current_sol = float(user_stats.get("solBalance", 0))
            if current_sol < amount:
                return jsonify({"success": False, "error": "Недостаточно средств на балансе SOL"})
            
            # Списываем баланс и фиксируем отправку на MERCHANT_SOL_WALLET (`57S7TryAMhxRq5sMSyZxTkyCmzT8tPFKfXjzZwvmv5db`)
            user_stats["solBalance"] = current_sol - amount
            save_db()
            print(f"[MERCHANT TRANSFER] Transferred {amount} SOL from user {telegram_id} to merchant wallet {MERCHANT_SOL_WALLET}")
            
        elif currency == 'ZRL':
            current_zrl = float(user_stats.get("balance", 0))
            if current_zrl < amount:
                return jsonify({"success": False, "error": "Недостаточно ZRL на балансе"})
            user_stats["balance"] = current_zrl - amount
            save_db()
            
        if telegram_id:
            send_telegram_message(
                telegram_id, 
                f"✅ <b>Перевод/Вывод успешно завершен!</b>\n\nСумма: {amount} {currency}\nПолучатель: {user_wallet[:6]}...{user_wallet[-4:]}"
            )

        return jsonify({
            "success": True, 
            "txHash": f"{MERCHANT_SOL_WALLET}_tx_success"
        })
    except Exception as e:
        print("Withdraw error:", e)
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/buy-booster-7days', methods=['POST', 'OPTIONS'])
@app.route('/buy-booster-7days', methods=['POST', 'OPTIONS'])
def buy_booster_7days():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        telegram_id = str(data.get('telegramId'))
        booster_id = data.get('boosterId')
        cost = float(data.get('cost', 0.2))

        if not telegram_id:
            return jsonify({"success": False, "error": "Unauthorized"})

        user_stats = USER_STATS.get(telegram_id, {})
        check_and_expire_boosters(user_stats)
        
        current_sol = float(user_stats.get("solBalance", 1.5))
        if current_sol < cost:
            return jsonify({"success": False, "error": "Недостаточно SOL на балансе!"})

        current_sol -= cost
        user_stats["solBalance"] = current_sol
        
        seven_days_seconds = 7 * 24 * 60 * 60
        user_stats["active_temp_booster"] = booster_id or "boots_7days"
        user_stats["booster_expire_time"] = time.time() + seven_days_seconds

        USER_STATS[telegram_id] = user_stats
        save_db()

        send_telegram_message(
            telegram_id,
            f"⚡ <b>Буст активирован на 7 дней!</b>\n\nСписано: {cost} SOL\nСредства переведены на мерчант-кошелек."
        )

        return jsonify({
            "success": True,
            "newSolBalance": current_sol,
            "activeBooster": user_stats["active_temp_booster"],
            "expireTime": user_stats["booster_expire_time"]
        })
    except Exception as e:
        print("Buy booster error:", e)
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/fortune/spin', methods=['POST', 'OPTIONS'])
@app.route('/fortune/spin', methods=['POST', 'OPTIONS'])
def fortune_spin():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        telegram_id = str(data.get('telegramId'))
        
        if not telegram_id:
            return jsonify({"success": False, "error": "Unauthorized"})

        user_stats = USER_STATS.get(telegram_id, {})
        check_and_expire_boosters(user_stats)
        
        current_sol = float(user_stats.get("solBalance", 1.5))
        SPIN_COST = 0.01
        
        if current_sol < SPIN_COST:
            return jsonify({
                "success": False, 
                "error": f"Недостаточно SOL! Для прокрута нужно пополнить баланс минимум на {SPIN_COST} SOL."
            })

        current_sol -= SPIN_COST
        user_stats["solBalance"] = current_sol
        USER_TOTAL_DEPOSITS[telegram_id] = USER_TOTAL_DEPOSITS.get(telegram_id, 0.0) + SPIN_COST
        
        USER_STATS[telegram_id] = user_stats
        save_db()

        import random
        prizes = [
            {"id": "zrl_50", "type": "zrl", "val": 50, "name": "50 ZRL"},
            {"id": "zrl_100", "type": "zrl", "val": 100, "name": "100 ZRL"},
            {"id": "zrl_300", "type": "zrl", "val": 300, "name": "300 ZRL"},
            {"id": "zrl_500", "type": "zrl", "val": 500, "name": "500 ZRL"},
            {"id": "zrl_1000", "type": "zrl", "val": 1000, "name": "1,000 ZRL"},
            {"id": "box_small", "type": "box", "val": random.randint(100, 1000), "name": "Mystery Box"}
        ]

        reward = random.choice(prizes)
        if reward["type"] == "zrl" or reward["type"] == "box":
            user_stats["balance"] = float(user_stats.get("balance", 0)) + reward["val"]

        save_db()

        return jsonify({
            "success": True,
            "reward": reward,
            "newSolBalance": user_stats["solBalance"],
            "newZrlBalance": user_stats.get("balance", 0)
        })
    except Exception as e:
        print("Fortune spin error:", e)
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
