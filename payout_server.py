import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from solana.rpc.api import Client

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

solana_client = Client("https://api.mainnet-beta.solana.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

DB_FILE = "database.json"

def load_db():
    default_data = {
        "FRIENDS_DB": {},
        "REQUESTS_DB": [],
        "USERS_MAP": {},
        "USER_STATS": {},
        "CHAT_DB": []
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
            "CHAT_DB": CHAT_DB
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
            USER_STATS[telegram_id] = {
                "username": username,
                "walletAddress": data.get('walletAddress'),
                "distance": data.get('distance', 0),
                "balance": data.get('balance', 0)
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
            "telegramId": uid
        })
    users_list.sort(key=lambda x: x['dist'], reverse=True)
    for i, u in enumerate(users_list):
        u['rank'] = i + 1
    return jsonify({"success": True, "leaderboard": users_list[:50]})

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
    
    # Отдаем заявки, адресованные конкретно этому Telegram ID
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

        # Ищем ID целевого пользователя по никнейму
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
    return jsonify({
        "success": True,
        "solBalance": 1.5,
        "usdcBalance": 50.0,
        "zrlBalance": 25000.0
    })

@app.route('/withdraw', methods=['POST', 'OPTIONS'])
@app.route('/api/withdraw', methods=['POST', 'OPTIONS'])
def withdraw():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json or {}
        user_wallet = data.get('wallet') or data.get('walletAddress')
        amount = float(data.get('amount', 0))
        if not user_wallet or amount <= 0:
            return jsonify({"success": False, "error": "Неверные данные кошелька или суммы"})
        return jsonify({
            "success": True, 
            "txHash": "5Vq7s...solana_tx_success_hash_simulation"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
