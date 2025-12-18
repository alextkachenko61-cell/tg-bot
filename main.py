import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
DATA_FILE = Path("data/users.json")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Please provide it in the environment or .env file.")

if not CHANNEL_USERNAME:
    raise RuntimeError(
        "CHANNEL_USERNAME is not set. Please provide it in the environment or .env file."
    )

router = Router()


def ensure_data_file() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text("{}", encoding="utf-8")


def load_users() -> Dict[str, Dict[str, Any]]:
    ensure_data_file()
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("User data file is corrupted. Resetting storage.")
        DATA_FILE.write_text("{}", encoding="utf-8")
        return {}


def save_users(users: Dict[str, Dict[str, Any]]) -> None:
    ensure_data_file()
    DATA_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def build_subscription_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    channel_link = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
    builder.button(text="Подписаться", url=channel_link)
    builder.button(text="Проверить подписку", callback_data="check_subscription")
    builder.adjust(1)
    return builder.as_markup()


def build_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Получить расклад")
    builder.button(text="Меню")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def build_spread_options_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Карта дня")
    builder.button(text="Расклад из 3 карт")
    return builder.as_markup(resize_keyboard=True)


def build_premium_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Premium")
    builder.button(text="Пригласить друга")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_user_record(user_id: int) -> Dict[str, Any]:
    users = load_users()
    user_key = str(user_id)
    user = users.get(user_key, {"spreads_left": 0, "free_granted": False})
    users[user_key] = user
    save_users(users)
    return user


def update_user_record(user_id: int, spreads_left: int, free_granted: bool) -> None:
    users = load_users()
    users[str(user_id)] = {"spreads_left": spreads_left, "free_granted": free_granted}
    save_users(users)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(
        "Для использования бота подпишитесь на канал",
        reply_markup=build_subscription_keyboard(),
    )


@router.callback_query(lambda c: c.data == "check_subscription")
async def handle_check_subscription(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    member = await bot.get_chat_member(CHANNEL_USERNAME, callback.from_user.id)
    status = member.status

    if status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        await callback.message.answer(
            "Для использования бота подпишитесь на канал",
            reply_markup=build_subscription_keyboard(),
        )
        return

    user = get_user_record(callback.from_user.id)
    spreads_left = user.get("spreads_left", 0)
    free_granted = user.get("free_granted", False)

    if not free_granted:
        spreads_left += 1
        free_granted = True
        update_user_record(callback.from_user.id, spreads_left, free_granted)

    await callback.message.answer(
        f"Вам начислен бесплатный расклад, раскладов доступно: {spreads_left}",
        reply_markup=build_menu_keyboard(),
    )


@router.message(F.text == "Меню")
async def handle_menu(message: Message) -> None:
    user = get_user_record(message.from_user.id)
    spreads_left = user.get("spreads_left", 0)

    await message.answer(
        f"Доступно раскладов: {spreads_left}",
        reply_markup=build_menu_keyboard(),
    )


@router.message(F.text == "Получить расклад")
async def handle_get_spread(message: Message) -> None:
    user = get_user_record(message.from_user.id)
    spreads_left = user.get("spreads_left", 0)

    if spreads_left <= 0:
        await message.answer(
            "К сожалению, у вас закончились расклады. Вы можете приобрести premium либо получить бесплатный расклад за каждого приглашенного друга.",
            reply_markup=build_premium_keyboard(),
        )
        return

    await message.answer(
        "Выберите тип расклада:",
        reply_markup=build_spread_options_keyboard(),
    )


@router.message(F.text.in_({"Карта дня", "Расклад из 3 карт"}))
async def handle_spread_choice(message: Message) -> None:
    user = get_user_record(message.from_user.id)
    spreads_left = user.get("spreads_left", 0)

    if spreads_left <= 0:
        await message.answer(
            "К сожалению, у вас закончились расклады. Вы можете приобрести premium либо получить бесплатный расклад за каждого приглашенного друга.",
            reply_markup=build_premium_keyboard(),
        )
        return

    spreads_left -= 1
    update_user_record(message.from_user.id, spreads_left, user.get("free_granted", False))

    if message.text == "Карта дня":
        text = "Карта дня: (заглушка) Значение будет добавлено позже"
    else:
        text = "3 карты: (заглушка) Значения будут добавлены позже"

    await message.answer(text, reply_markup=build_menu_keyboard())


@router.message(F.text == "Premium")
async def handle_premium(message: Message) -> None:
    await message.answer("Premium скоро будет доступен.", reply_markup=build_menu_keyboard())


@router.message(F.text == "Пригласить друга")
async def handle_invite_friend(message: Message) -> None:
    await message.answer("Скоро добавим реферальную систему.", reply_markup=build_menu_keyboard())


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
