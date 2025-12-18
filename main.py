import asyncio
import json
import logging
import os
import random
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
DATA_FILE = Path("data/users.json")
CARDS_DIR = Path("assets/cards")
CARD_EXTENSIONS = {".png", ".jpg", ".jpeg"}

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


def load_card_files() -> List[Path]:
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    return [
        path
        for path in CARDS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in CARD_EXTENSIONS
    ]


def create_three_card_collage(card_paths: List[Path]) -> BufferedInputFile:
    images = []
    for path in card_paths:
        with Image.open(path) as img:
            images.append(img.convert("RGB"))

    target_height = max(image.height for image in images)
    resized_images = []
    for image in images:
        if image.height != target_height:
            new_width = int(image.width * (target_height / image.height))
            resized_images.append(image.resize((new_width, target_height)))
        else:
            resized_images.append(image)

    total_width = sum(image.width for image in resized_images)
    collage = Image.new("RGB", (total_width, target_height))
    offset = 0
    for image in resized_images:
        collage.paste(image, (offset, 0))
        offset += image.width

    buffer = BytesIO()
    collage.save(buffer, format="JPEG")
    buffer.seek(0)
    return BufferedInputFile(buffer.getvalue(), filename="three_cards.jpg")


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

    card_files = load_card_files()
    if not card_files:
        await message.answer(
            "Нет карт в базе, добавьте изображения в assets/cards.",
            reply_markup=build_menu_keyboard(),
        )
        return

    if message.text == "Карта дня":
        card_path = random.choice(card_files)
        await message.answer_photo(FSInputFile(card_path))
        spreads_left -= 1
        update_user_record(message.from_user.id, spreads_left, user.get("free_granted", False))
        await message.answer(
            f"Карта дня: {card_path.stem}. Интерпретация будет добавлена позже.",
            reply_markup=build_menu_keyboard(),
        )
        return

    if len(card_files) < 3:
        await message.answer(
            "Недостаточно карт в базе, добавьте не менее 3 изображений в assets/cards.",
            reply_markup=build_menu_keyboard(),
        )
        return

    selected_cards = random.sample(card_files, 3)
    collage_file = create_three_card_collage(selected_cards)
    await message.answer_photo(collage_file)
    spreads_left -= 1
    update_user_record(message.from_user.id, spreads_left, user.get("free_granted", False))
    card_names = ", ".join(card.stem for card in selected_cards)
    await message.answer(
        f"3 карты: {card_names}. Интерпретации будут добавлены позже.",
        reply_markup=build_menu_keyboard(),
    )


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
