import json
from aiogram import types
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

# Команда для вызова мини-приложения
@dp.message(commands=["run"])
async def cmd_run(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏃 Запустить Zer0Life Run", web_app=WebAppInfo(url="https://wlodek2106-cyber.github.io/Zer0life_ai/"))]
    ])
    await message.answer("Откройте мини-приложение, чтобы начать пробежку и заработать по 500 ZRL за километр:", reply_markup=keyboard)

# Автоматический прием данных по завершении тренировки
@dp.message(content_types=["web_app_data"])
async def handle_web_app_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        distance = data.get("distance")
        reward = data.get("reward")
        
        user_id = message.from_user.id
        
        # Здесь пишется логика записи `reward` на баланс пользователя в базе данных
        
        await message.answer(
            f"Отличная тренировка! 🏁\n"
            f"Пройдено: {distance} км\n"
            f"Начислено на баланс: <b>{reward} ZRL</b>"
        )
    except Exception as e:
        await message.answer("Не удалось обработать результаты тренировки.")
