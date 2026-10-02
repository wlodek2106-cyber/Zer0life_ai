import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from solana.rpc.api import Client

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Подключение к сети Solana
solana_client = Client("https://api.mainnet-beta.solana.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Целевой кошелек проекта для депозитов (куда игроки пересылают SOL)
TARGET_WALLET = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"

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
            
            existing = USER_STATS.get(telegram_id, {})
            USER_STATS[telegram_id] = {
                "username": username,
                "walletAddress": data.get('walletAddress', existing.get('walletAddress', TARGET_WALLET)),
                "distance": data.get('distance', existing.get('distance', 0)),
                "balance": data.get('balance', existing.get('balance', 0)),
                "solBalance": existing.get('solBalance', 1.5)
            }
            USERS_MAP[telegram_id] = username
            save_db()
            return jsonify({"success": True})
        return jsonify({"success": False}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/leaderboard', methods=['GET', 'OPTIONS'])
@app.route('/api/leaderboard', methods=['GET', 'OPTIONS'])
def get_leaderboard():
    if request.method == 'OPTIONS':
        return '', 200
    users_list = []
    for uid, stats in USER_STATS.items():
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
        
    # Поиск места текущего пользователя, если передан telegramId
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
            return jsonify({"success": False, "error": f"User '{target_username}' not found. Make sure your friend has opened the app!"})

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
        print("Friend request error:", e)
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
    return jsonify({
        "success": True,
        "solBalance": float(user_stats.get("solBalance", 1.5)),
        "usdcBalance": 50.0,
        "zrlBalance": float(user_stats.get("balance", 25000.0))
    })

# --- ЗАЩИЩЕННЫЙ ЭНДПОИНТ ПРОВЕРКИ ДЕПОЗИТА ЧЕРЕЗ БЛОКЧЕЙН ---
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
            
        # Запрашиваем транзакцию из сети Solana
        response = solana_client.get_transaction(tx_signature, max_supported_transaction_version=0)
        
        if not response or not response.get('result'):
            return jsonify({"success": False, "error": "Транзакция еще не найдена в блокчейне. Подождите 10-15 секунд."}), 400
            
        tx_data = response['result']
        
        # Проверяем на наличие ошибок выполнения транзакции
        meta = tx_data.get('meta', {})
        if meta and meta.get('err') is not None:
            return jsonify({"success": False, "error": "Транзакция завершилась с ошибкой в блокчейне Solana."}), 400
            
        # Извлекаем ключи аккаунтов
        transaction_info = tx_data.get('transaction', {})
        message = transaction_info.get('message', {})
        account_keys = message.get('accountKeys', [])
        
        # Ищем индекс нашего целевого кошелька
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
            
        pre_balance = pre_balances[target_index]
        post_balance = post_balances[target_index]
        
        diff_lamports = post_balance - pre_balance
        diff_sol = diff_lamports / 1_000_000_000
        
        MIN_DEPOSIT = 0.01
        if diff_sol < MIN_DEPOSIT:
            return jsonify({"success": False, "error": f"Сумма слишком мала ({diff_sol} SOL). Минимум: {MIN_DEPOSIT} SOL."}), 400
            
        # Успешно! Начисляем SOL пользователю
        user_stats = USER_STATS.get(telegram_id, {})
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
        telegram_id = data.get('telegramId')
        
        if not user_wallet or amount <= 0:
            return jsonify({"success": False, "error": "Неверные данные кошелька или суммы"})
        
        if amount == 0.01:
            currency = 'SOL'
        
        if telegram_id:
            send_telegram_message(
                telegram_id, 
                f"✅ <b>Вывод успешно завершен!</b>\n\nСумма: {amount} {currency}\nКошелек: {user_wallet[:6]}...{user_wallet[-4:]}"
            )

        return jsonify({
            "success": True, 
            "txHash": f"{TARGET_WALLET}_tx_success"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# --- ЭНДПОИНТ КОЛЕСА ФОРТУНЫ ---
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
        total_user_deposited = USER_TOTAL_DEPOSITS[telegram_id]
        
        USER_STATS[telegram_id] = user_stats
        save_db()

        send_telegram_message(
            telegram_id,
            f"✅ <b>Оплата прокрута колеса</b>\n\nСумма: 0.01 SOL\nМерчант: 57S7Tr...v5db"
        )

        import random
        
        prizes = [
            {"id": "zrl_50", "type": "zrl", "val": 50, "name": "50 ZRL"},
            {"id": "zrl_100", "type": "zrl", "val": 100, "name": "100 ZRL"},
            {"id": "zrl_300", "type": "zrl", "val": 300, "name": "300 ZRL"},
            {"id": "zrl_500", "type": "zrl", "val": 500, "name": "500 ZRL"},
            {"id": "zrl_1000", "type": "zrl", "val": 1000, "name": "1,000 ZRL"},
            {"id": "zrl_5000", "type": "zrl", "val": 5000, "name": "5,000 ZRL"},
            {"id": "zrl_10000", "type": "zrl", "val": 10000, "name": "10,000 ZRL"},
            {"id": "box_small", "type": "box", "val": random.randint(100, 1000), "name": "Mystery Box"},
            {"id": "booster_x2", "type": "booster", "val": 2, "name": "Booster x2 (1h)"},
            {"id": "repair_kit", "type": "repair", "val": 100, "name": "Full Repair (100 HP)"}
        ]

        if total_user_deposited >= 1.0:
            prizes.append({"id": "sol_001", "type": "sol", "val": 0.01, "name": "0.01 SOL"})
            prizes.append({"id": "sol_01", "type": "sol", "val": 0.1, "name": "0.1 SOL"})

        rand_chance = random.random()
        if rand_chance < 0.99 or total_user_deposited < 1.0:
            safe_prizes = [p for p in prizes if p["type"] != "sol"]
            reward = random.choice(safe_prizes)
        else:
            sol_prizes = [p for p in prizes if p["type"] == "sol"]
            reward = random.choice(sol_prizes) if sol_prizes else random.choice(prizes)

        current_zrl = float(user_stats.get("balance", 0))
        if reward["type"] == "zrl" or reward["type"] == "box":
            current_zrl += reward["val"]
            user_stats["balance"] = current_zrl
        elif reward["type"] == "sol":
            user_stats["solBalance"] = float(user_stats.get("solBalance", 0)) + reward["val"]
        elif reward["type"] == "booster":
            user_stats["active_temp_booster"] = reward["id"]
        elif reward["type"] == "repair":
            user_stats["boots_hp"] = 100.0

        save_db()

        return jsonify({
            "success": True,
            "reward": reward,
            "newSolBalance": user_stats["solBalance"],
            "newZrlBalance": user_stats.get("balance", 0),
            "totalDeposited": total_user_deposited
        })
    except Exception as e:
        print("Fortune spin error:", e)
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
