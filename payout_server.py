import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from spl.token.client import Token
from spl.token.instructions import transfer, TransferParams

app = Flask(__name__)
CORS(app)

# Подключаемся к сети Solana (Mainnet)
solana_client = Client("https://api.mainnet-beta.solana.com")

# Получаем ключи из защищенных переменных сервера Render
PRIVATE_KEY_BYTES = os.getenv("MASTER_WALLET_PRIVATE_KEY") # Массив байтов или base58
ZRL_MINT_STR = os.getenv("ZRL_MINT")

@app.route('/api/withdraw', methods=['POST'])
def withdraw():
    try:
        data = request.json
        user_wallet = data.get('wallet')
        amount = float(data.get('amount'))

        if not user_wallet or amount <= 0:
            return jsonify({"success": False, "error": "Неверные данные кошелька или суммы"})

        # Здесь происходит реальная отправка токенов с мастер-кошелька
        # (Полная логика подписи транзакции Solana SPL-токена)
        
        # Для теста пока возвращаем успех с симуляцией хэша, 
        # сейчас добавим правильный импорт ключа в зависимости от формата
        
        return jsonify({
            "success": True, 
            "txHash": "5Vq7s...тестовый_хэш_транзакции_solana"
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
