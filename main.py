import asyncio
import json
import logging
import os
import random
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ChatMemberStatus, ParseMode
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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from PIL import Image
from openai import AsyncOpenAI

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_ENABLED = os.getenv("LLM_ENABLED", "1") == "1"
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1-mini")
LLM_MAX_TOKENS_DAY = int(os.getenv("LLM_MAX_TOKENS_DAY", "220"))
LLM_MAX_TOKENS_3 = int(os.getenv("LLM_MAX_TOKENS_3", "420"))
LLM_SYSTEM_PROMPT = os.getenv(
    "LLM_SYSTEM_PROMPT",
    (
        "Ты помогаешь кратко и нейтрально интерпретировать карты Таро. "
        "Отвечай на русском языке без мистики и пафоса, лаконично и спокойно."
    ),
)
DATA_FILE = Path("data/users.json")
CARDS_DIR = Path("assets/cards")
CARD_EXTENSIONS = {".png", ".jpg", ".jpeg"}
DEFAULT_USER = {
    "spreads_left": 0,
    "free_granted": False,
    "invited_count": 0,
    "referred_by": None,
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Please provide it in the environment or .env file.")

if not CHANNEL_USERNAME:
    raise RuntimeError(
        "CHANNEL_USERNAME is not set. Please provide it in the environment or .env file."
    )

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

router = Router()


class SpreadStates(StatesGroup):
    waiting_for_question = State()


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


def ensure_user_defaults(user: Dict[str, Any]) -> Dict[str, Any]:
    updated = {**DEFAULT_USER, **(user or {})}
    return updated


def save_user_record(user_id: int, user: Dict[str, Any]) -> None:
    users = load_users()
    users[str(user_id)] = ensure_user_defaults(user)
    save_users(users)


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


def build_cancel_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Отмена")
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
    user = ensure_user_defaults(users.get(user_key, {}))
    if users.get(user_key) != user:
        users[user_key] = user
        save_users(users)
    return user


def parse_referral_id(args: str) -> Optional[int]:
    payload = args.strip()
    if not payload.isdigit():
        return None
    return int(payload)


def extract_start_payload(message: Message) -> str:
    if not message.text:
        return ""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1]


async def call_llm(messages: List[Dict[str, str]], max_tokens: int) -> Optional[str]:
    if not (LLM_ENABLED and openai_client):
        return None

    try:
        response = await openai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content if response.choices else None
    except Exception as exc:  # noqa: BLE001
        logging.warning("Не удалось получить ответ от LLM: %s", exc)
        return None


async def generate_card_day_interpretation(card_name: str) -> str:
    fallback = (
        f"<b>Карта дня:</b> {card_name}. " "Интерпретация будет добавлена позже."
    )
    messages = [
        {"role": "system", "content": LLM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Контекст: Карта дня. Название карты: "
                f"{card_name}. Ответ верни в HTML, выдели жирным краткий вывод."
            ),
        },
    ]

    text = await call_llm(messages=messages, max_tokens=LLM_MAX_TOKENS_DAY)
    return text or fallback


async def generate_three_cards_interpretation(question: str, card_names: List[str]) -> str:
    joined_cards = ", ".join(card_names)
    messages = [
        {"role": "system", "content": LLM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Вопрос пользователя: {question}\n"
                f"Карты: {joined_cards}."
                " Опиши значение каждой карты и общий итог."
                " Ответ верни в HTML, выдели жирным ключевые выводы."
            ),
        },
    ]

    fallback = (
        "<b>Интерпретация недоступна.</b> " "Позже добавим подробности по раскладу."
    )
    text = await call_llm(messages=messages, max_tokens=LLM_MAX_TOKENS_3)
    return text or fallback


@router.message(CommandStart())
async def handle_start(message: Message, bot: Bot) -> None:
    users = load_users()
    user_id = message.from_user.id
    user_key = str(user_id)
    is_new_user = user_key not in users
    payload_text = extract_start_payload(message)
    referral_payload = parse_referral_id(payload_text) if payload_text else None

    if is_new_user:
        new_user_record = ensure_user_defaults({})
        if referral_payload and referral_payload != user_id:
            inviter_key = str(referral_payload)
            inviter_record = ensure_user_defaults(users.get(inviter_key, {}))
            inviter_record["spreads_left"] += 1
            inviter_record["invited_count"] += 1
            users[inviter_key] = inviter_record
            new_user_record["referred_by"] = referral_payload
            try:
                await bot.send_message(
                    referral_payload,
                    f"Вам начислен бесплатный расклад за приглашенного друга. Доступно: {inviter_record['spreads_left']}",
                )
            except Exception as exc:  # noqa: BLE001
                logging.info("Не удалось отправить уведомление приглашавшему %s: %s", referral_payload, exc)

        users[user_key] = new_user_record
        save_users(users)
    else:
        current_user = ensure_user_defaults(users.get(user_key, {}))
        if users.get(user_key) != current_user:
            users[user_key] = current_user
            save_users(users)

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
        user["spreads_left"] = spreads_left
        user["free_granted"] = free_granted
        save_user_record(callback.from_user.id, user)

    await callback.message.answer(
        f"Вам начислен бесплатный расклад, раскладов доступно: {spreads_left}",
        reply_markup=build_menu_keyboard(),
    )


@router.message(F.text == "Меню")
async def handle_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_user_record(message.from_user.id)
    spreads_left = user.get("spreads_left", 0)

    await message.answer(
        f"Доступно раскладов: {spreads_left}",
        reply_markup=build_menu_keyboard(),
    )


@router.message(F.text == "Получить расклад")
async def handle_get_spread(message: Message, state: FSMContext) -> None:
    await state.clear()
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


async def process_card_of_day(message: Message, user: Dict[str, Any], card_files: List[Path]) -> None:
    card_path = random.choice(card_files)
    await message.answer_photo(FSInputFile(card_path))
    interpretation = await generate_card_day_interpretation(card_path.stem)
    await message.answer(
        interpretation,
        reply_markup=build_menu_keyboard(),
    )
    user["spreads_left"] = max(user.get("spreads_left", 0) - 1, 0)
    save_user_record(message.from_user.id, user)


@router.message(F.text.in_({"Карта дня", "Расклад из 3 карт"}))
async def handle_spread_choice(message: Message, state: FSMContext) -> None:
    await state.clear()
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
        await process_card_of_day(message, user, card_files)
        return

    if len(card_files) < 3:
        await message.answer(
            "Недостаточно карт в базе, добавьте не менее 3 изображений в assets/cards.",
            reply_markup=build_menu_keyboard(),
        )
        return

    await state.set_state(SpreadStates.waiting_for_question)
    await message.answer(
        "Напишите ваш вопрос одним сообщением.",
        reply_markup=build_cancel_keyboard(),
    )


@router.message(F.text == "Premium")
async def handle_premium(message: Message) -> None:
    await message.answer("Premium скоро будет доступен.", reply_markup=build_menu_keyboard())


@router.message(F.text == "Пригласить друга")
async def handle_invite_friend(message: Message, bot: Bot) -> None:
    me = await bot.get_me()
    bot_username = me.username
    if not bot_username:
        await message.answer("Не удалось получить имя бота для ссылки.", reply_markup=build_menu_keyboard())
        return

    referral_link = f"https://t.me/{bot_username}?start={message.from_user.id}"
    await message.answer(
        "Поделитесь ссылкой с другом, чтобы получить дополнительный расклад:\n" f"{referral_link}",
        reply_markup=build_menu_keyboard(),
    )


@router.message(SpreadStates.waiting_for_question, F.text == "Отмена")
async def handle_cancel_question(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=build_menu_keyboard())


@router.message(SpreadStates.waiting_for_question)
async def handle_three_card_question(message: Message, state: FSMContext) -> None:
    user = get_user_record(message.from_user.id)
    spreads_left = user.get("spreads_left", 0)

    if spreads_left <= 0:
        await state.clear()
        await message.answer(
            "К сожалению, у вас закончились расклады. Вы можете приобрести premium либо получить бесплатный расклад за каждого пригласившего друга.",
            reply_markup=build_premium_keyboard(),
        )
        return

    card_files = load_card_files()
    if len(card_files) < 3:
        await state.clear()
        await message.answer(
            "Недостаточно карт в базе, добавьте не менее 3 изображений в assets/cards.",
            reply_markup=build_menu_keyboard(),
        )
        return

    selected_cards = random.sample(card_files, 3)
    collage_file = create_three_card_collage(selected_cards)
    await message.answer_photo(collage_file)

    card_names = [card.stem for card in selected_cards]
    question_text = message.text or ""
    interpretation = await generate_three_cards_interpretation(question_text, card_names)
    await message.answer(interpretation, reply_markup=build_menu_keyboard())

    user["spreads_left"] = max(spreads_left - 1, 0)
    save_user_record(message.from_user.id, user)
    await state.clear()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
