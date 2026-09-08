import os
import requests

# Получаем токен или ключ API из переменных окружения Render
ETSY_API_KEY = os.getenv("ETSY_API_KEY")

def get_etsy_items(query):
    """Пример функции для поиска товаров на Etsy"""
    url = f"https://openapi.etsy.com/v3/application/listings/active?keywords={query}"
    headers = {
        "x-api-key": ETSY_API_KEY
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get("results", [])
        else:
            return []
    except Exception as e:
        print(f"Ошибка при запросе к Etsy: {e}")
        return []
