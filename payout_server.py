import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from solana.rpc.api import Client

app = Flask(__name__)
CORS(app)

# Подключаемся к сети Solana (Mainnet)
solana_client = Client("https://api.mainnet-beta.solana.com")

# Токен твоего Telegram-бота (нужно добавить в переменные окружения Render как TELEGRAM_BOT_TOKEN)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- Система постоянного сохранения данных (JSON файл) ---
DB_FILE = "database.json"

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("Error loading DB:", e)
    return {
        "FRIENDS_DB": {},
        "REQUESTS_DB": {},
        "USERS_MAP": {},
        "USER_STATS": {}
    }

def save_db():
    try:
        data = {
            "FRIENDS_DB": FRIENDS_DB,
            "REQUESTS_DB": REQUESTS_DB,
            "USERS_MAP": USERS_MAP,
            "USER_STATS": USER_STATS
        }
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Error saving DB:", e)

# Загружаем данные при старте сервера
db = load_db()
FRIENDS_DB = db.get("FRIENDS_DB", {})
REQUESTS_DB = db.get("REQUESTS_DB", {})
USERS_MAP = db.get("USERS_MAP", {})
USER_STATS = db.get("USER_STATS", {})

def send_telegram_message(chat_id, text):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
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

@app.route('/register', methods=['POST'])
def register_user():
    data = request.json or {}
    telegram_id = str(data.get('telegramId'))
    username = data.get('username')
    wallet_address = data.get('walletAddress')
    
    if telegram_id and username:
        USERS_MAP[telegram_id] = username
        save_db()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid data"}), 400

@app.route('/update-stats', methods=['POST'])
def update_stats():
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

@app.route('/leaderboard', methods=['GET'])
def get_leaderboard():
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
        
    return jsonify({
        "success": True,
        "leaderboard": users_list[:50]
    })

# --- Эндпоинты для Друзей и Чата ---

@app.route('/api/friend/list', methods=['GET'])
def get_friends_list():
    telegram_id = str(request.args.get('telegramId'))
    friends_ids = FRIENDS_DB.get(telegram_id, [])
    
    friends = []
    for fid in friends_ids:
        st = USER_STATS.get(fid, {})
        friends.append({
            "telegramId": fid, 
            "username": st.get("username", USERS_MAP.get(fid, "Friend")), 
            "distance": st.get("distance", 0.0)
        })
    
    username = USERS_MAP.get(telegram_id, '')
    my_requests = REQUESTS_DB.get(username, [])
    
    return jsonify({"success": True, "friends": friends, "requests": my_requests})

@app.route('/api/friend/request', methods=['POST'])
def send_friend_request():
    data = request.json or {}
    from_id = str(data.get('fromId'))
    from_username = data.get('fromUsername')
    target_username = data.get('targetUsername', '').strip()
    
    if not target_username:
        return jsonify({"success": False, "error": "Введите никнейм"}), 400
        
    USERS_MAP[from_id] = from_username
    
    # Ищем target по никнейму среди всех зарегистрированных
    target_id = None
    for uid, uname in USERS_MAP.items():
        if uname and uname.lower() == target_username.lower():
            target_id = uid
            break
            
    if not target_id:
        return jsonify({"success": False, "error": "Игрок с таким никнеймом не найден"}), 404
        
    if target_username not in REQUESTS_DB:
        REQUESTS_DB[target_username] = []
        
    existing = [r for r in REQUESTS_DB[target_username] if str(r['telegramId']) == str(from_id)]
    if not existing:
        REQUESTS_DB[target_username].append({"telegramId": from_id, "username": from_username})
        save_db()
        
        # Отправляем уведомление в Telegram пользователю
        msg_text = f"👥 <b>Новая заявка в друзья!</b>\n\nИгрок <b>{from_username}</b> хочет добавить вас в друзья в <b>Zer0life Run</b>."
        send_telegram_message(target_id, msg_text)
        
    return jsonify({"success": True})

@app.route('/api/friend/accept', methods=['POST'])
def accept_friend_request():
    data = request.json or {}
    user_id = str(data.get('userId'))
    friend_id = str(data.get('friendId'))
    
    if user_id not in FRIENDS_DB: FRIENDS_DB[user_id] = []
    if friend_id not in FRIENDS_DB: FRIENDS_DB[friend_id] = []
    
    if friend_id not in FRIENDS_DB[user_id]: FRIENDS_DB[user_id].append(friend_id)
    if user_id not in FRIENDS_DB[friend_id]: FRIENDS_DB[friend_id].append(user_id)
    
    my_username = USERS_MAP.get(user_id, '')
    if my_username in REQUESTS_DB:
        REQUESTS_DB[my_username] = [r for r in REQUESTS_DB[my_username] if str(r['telegramId']) != friend_id]
        
    save_db()
    return jsonify({"success": True})

@app.route('/api/chat/messages', methods=['GET'])
def get_chat_messages():
    user1 = str(request.args.get('user1'))
    user2 = str(request.args.get('user2'))
    
    messages = [
        m for m in CHAT_DB 
        if (str(m['senderId']) == user1 and str(m['receiverId']) == user2) or 
           (str(m['senderId']) == user2 and str(m['receiverId']) == user1)
    ]
    return jsonify({"success": True, "messages": messages})

@app.route('/api/chat/send', methods=['POST'])
def send_chat_message():
    data = request.json or {}
    msg = {
        "senderId": str(data.get('senderId')),
        "receiverId": str(data.get('receiverId')),
        "text": data.get('text'),
        "timestamp": data.get('timestamp', 0)
    }
    CHAT_DB.append(msg)
    # Ограничиваем историю чата последними 500 сообщениями, чтобы файл не раздувался
    if len(CHAT_DB) > 500:
        CHAT_DB.pop(0)
    save_db()
    return jsonify({"success": True})

# --- Эндпоинты Кошелька и Вывода ---

@app.route('/check-deposit', methods=['POST'])
def check_deposit():
    return jsonify({
        "success": True,
        "solBalance": 1.5,
        "usdcBalance": 50.0,
        "zrlBalance": 25000.0
    })

@app.route('/api/withdraw', methods=['POST'])
def withdraw():
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
