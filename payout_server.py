import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.system_program import transfer as sys_transfer, TransferParams as SysTransferParams
import base58

app = Flask(__name__)
CORS(app)

# Подключаемся к сети Solana (Mainnet)
solana_client = Client("https://api.mainnet-beta.solana.com")

# Получаем ключи из защищенных переменных сервера Render
MASTER_WALLET_PRIVATE_KEY = os.getenv("MASTER_WALLET_PRIVATE_KEY")
ZRL_MINT_STR = os.getenv("ZRL_MINT")

# Временные базы данных в памяти для профилей, друзей и чата
FRIENDS_DB = {}   # { telegramId: [friendId1, friendId2] }
REQUESTS_DB = {}  # { targetUsername: [ {fromId, fromUsername} ] }
USERS_MAP = {}    # { telegramId: username }
CHAT_DB = []      # [ {senderId, receiverId, text, timestamp} ]
USER_WALLETS = {} # { telegramId: walletAddress }
USER_STATS = {}   # { telegramId: {distance, balance} }

@app.route('/register', methods=['POST'])
def register_user():
    data = request.json or {}
    telegram_id = str(data.get('telegramId'))
    username = data.get('username')
    wallet_address = data.get('walletAddress')
    
    if telegram_id and username:
        USERS_MAP[telegram_id] = username
        if wallet_address:
            USER_WALLETS[telegram_id] = wallet_address
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid data"}), 400

@app.route('/update-stats', methods=['POST'])
def update_stats():
    data = request.json or {}
    telegram_id = str(data.get('telegramId'))
    if telegram_id:
        USER_STATS[telegram_id] = {
            "username": data.get('username'),
            "walletAddress": data.get('walletAddress'),
            "distance": data.get('distance', 0),
            "balance": data.get('balance', 0)
        }
        USERS_MAP[telegram_id] = data.get('username', USERS_MAP.get(telegram_id, 'Runner'))
        return jsonify({"success": True})
    return jsonify({"success": False}), 400

@app.route('/leaderboard', methods=['GET'])
def get_leaderboard():
    period = request.args.get('period', 'daily')
    users_list = []
    
    for uid, stats in USER_STATS.items():
        users_list.append({
            "name": stats.get("username", "Runner"),
            "dist": round(stats.get("distance", 0), 2),
            "zrl": int(stats.get("balance", 0)),
            "telegramId": uid
        })
        
    # Сортируем по дистанции
    users_list.sort(key=lambda x: x['dist'], reverse=True)
    
    # Расставляем ранги
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
    
    USERS_MAP[from_id] = from_username
    
    target_id = None
    for uid, uname in USERS_MAP.items():
        if uname.lower() == target_username.lower():
            target_id = uid
            break
            
    if not target_id:
        return jsonify({"success": False, "error": "User not found"}), 404
        
    if target_username not in REQUESTS_DB:
        REQUESTS_DB[target_username] = []
        
    existing = [r for r in REQUESTS_DB[target_username] if str(r['telegramId']) == str(from_id)]
    if not existing:
        REQUESTS_DB[target_username].append({"telegramId": from_id, "username": from_username})
        
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
    return jsonify({"success": True})

# --- Эндпоинты Кошелька и Вывода ---

@app.route('/check-deposit', methods=['POST'])
def check_deposit():
    data = request.json or {}
    wallet_address = data.get('walletAddress')
    
    # Возвращаем симуляцию балансов для кошелька
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

        # Логика реального или тестового перевода
        return jsonify({
            "success": True, 
            "txHash": "5Vq7s...solana_tx_success_hash_simulation"
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
