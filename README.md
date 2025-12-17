# Telegram Tarot Bot

## Настройка

1. Скопируйте файл окружения и укажите токен бота:
   ```bash
   cp .env.example .env
   ```
   Заполните `BOT_TOKEN` токеном, выданным [BotFather](https://t.me/BotFather).

2. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```

## Запуск

Запустите бота в режиме polling:
```bash
python main.py
```

Доступные команды:
- `/start` — приветственное сообщение.
