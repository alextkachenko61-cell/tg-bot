import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
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
DEFAULT_SYSTEM_PROMPT = (
    "Ты помогаешь кратко и нейтрально интерпретировать карты Таро. "
    "Отвечай на русском языке без мистики и пафоса, лаконично и спокойно."
)
LLM_SYSTEM_PROMPT = os.getenv("LLM_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)
LLM_SYSTEM_PROMPT_DAY = os.getenv("LLM_SYSTEM_PROMPT_DAY")
LLM_SYSTEM_PROMPT_3 = os.getenv("LLM_SYSTEM_PROMPT_3")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.4"))
LLM_TOP_P = float(os.getenv("LLM_TOP_P", "1.0"))
LLM_FREQUENCY_PENALTY = float(os.getenv("LLM_FREQUENCY_PENALTY", "0.2"))
LLM_PRESENCE_PENALTY = float(os.getenv("LLM_PRESENCE_PENALTY", "0.0"))
LLM_SEED = os.getenv("LLM_SEED")
DAILY_SPREAD_COOLDOWN = timedelta(hours=24)
DAILY_GIFT_COOLDOWN = timedelta(hours=24)
CLARIFY_COST = 10
DATA_FILE = Path("data/users.json")
CARDS_DIR = Path("assets/cards")
CARD_EXTENSIONS = {".png", ".jpg", ".jpeg"}
DEFAULT_USER = {
    "spreads_left": 0,
    "free_granted": False,
    "invited_count": 0,
    "referred_by": None,
    "registration_date": None,
    "diamonds": 0,
    "last_daily_spread_at": None,
    "last_daily_gift_at": None,
    "daily_spread_count": 0,
    "last_daily_card": None,
}
RELATION_SPREADS = [
    "Есть ли у него другая?",
    "Изменял ли он мне?",
    "Любит ли он меня на самом деле?",
    "Считает ли он меня «своей женщиной»?",
    "Уйдёт ли он от меня?",
]
FINANCE_SPREADS = [
    "Будут ли у меня деньги в ближайшее время?",
    "Почему деньги не задерживаются?",
    "Тратить на себя или экономить?",
    "Найду ли я того кто меня обеспечит?",
]
SELF_SPREADS = [
    "Где ты врёшь себе",
    "Что тебя реально сдерживает",
    "Чего ты на самом деле хочешь",
    "В чём твой внутренний конфликт",
    "Какую роль ты сейчас играешь",
]

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
    waiting_for_clarify = State()


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
    if not updated.get("registration_date"):
        updated["registration_date"] = datetime.now(timezone.utc).isoformat()
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


def build_start_journey_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Начать путешествие 🔮", callback_data="start_journey")
    builder.adjust(1)
    return builder.as_markup()


def build_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔮 Получить расклад")
    builder.button(text="Получить 💎")
    builder.button(text="Профиль")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def build_spread_entry_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="🃏 Расклад дня")
    builder.button(text="🗝️ Продвинутые расклады")
    builder.button(text="⬅️ В меню")
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


def build_clarify_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Уточняющий вопрос 10💎")
    builder.button(text="⬅️ В меню")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def build_advanced_spread_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Расклад из 3 карт")
    builder.button(text="⬅️ В меню")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def build_gift_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Крутить слот 🎰", callback_data="roll_daily_gift")
    builder.adjust(1)
    return builder.as_markup()


def build_spread_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🃏 Расклад дня", callback_data="spread_daily")
    builder.button(text="🗝️ Продвинутые расклады", callback_data="spread_advanced")
    builder.button(text="⬅️ Назад", callback_data="spread_back")
    builder.adjust(1)
    return builder.as_markup()


def build_advanced_categories_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❤️ Отношения", callback_data="adv_relations")
    builder.button(text="💰 Финансы", callback_data="adv_finance")
    builder.button(text="🪞 Про себя", callback_data="adv_self")
    builder.button(text="⬅️ Назад", callback_data="spread_menu")
    builder.adjust(1)
    return builder.as_markup()


def build_leaf_keyboard(options: List[str], prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, option in enumerate(options):
        builder.button(text=option, callback_data=f"{prefix}:{idx}")
    builder.button(text="⬅️ Назад", callback_data="spread_advanced")
    builder.adjust(1)
    return builder.as_markup()


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


def render_markers_to_html(text: str) -> str:
    return text.replace("[B]", "<b>").replace("[/B]", "</b>")


def iso_to_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_on_cooldown(last_ts: Optional[str], cooldown: timedelta) -> tuple[bool, int]:
    last_dt = iso_to_datetime(last_ts)
    if not last_dt:
        return False, 0
    elapsed = now_utc() - last_dt
    remaining = cooldown - elapsed
    remaining_seconds = int(remaining.total_seconds())
    return remaining_seconds > 0, max(0, remaining_seconds)


def format_remaining(seconds: int) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}ч")
    if minutes or not parts:
        parts.append(f"{minutes}м")
    return " ".join(parts)


def evaluate_slot_reward(value: int) -> tuple[int, str]:
    if value >= 64:
        return 30, "💎 Джекпот — Жабка даёт 30 кристалликов"
    if value >= 50:
        return 15, "🎰 Три одинаковых — Жабка даёт 15 кристалликов"
    return 5, "❌ Не совпало — Жабка даёт 5 кристалликов"


def format_profile_text(user: Dict[str, Any]) -> str:
    reg_dt = iso_to_datetime(user.get("registration_date"))
    reg_str = reg_dt.strftime("%Y-%m-%d %H:%M UTC") if reg_dt else "неизвестно"
    diamonds = user.get("diamonds", 0)
    invited = user.get("invited_count", 0)
    daily_count = user.get("daily_spread_count", 0)
    spreads_left = user.get("spreads_left", 0)
    last_daily_card = user.get("last_daily_card")
    daily_card_text = last_daily_card or "ещё не было"
    return (
        "[B]Профиль[/B]\n"
        f"Дата регистрации: {reg_str}\n"
        f"Алмазики: {diamonds}💎\n"
        f"Приглашённых друзей: {invited}\n"
        f"Получено раскладов дня: {daily_count}\n"
        f"Доступно раскладов: {spreads_left}\n"
        f"Последняя карта дня: {daily_card_text}"
    )


def get_system_prompt(mode: str) -> str:
    if mode == "DAY":
        return LLM_SYSTEM_PROMPT_DAY or LLM_SYSTEM_PROMPT or DEFAULT_SYSTEM_PROMPT
    if mode == "THREE":
        return LLM_SYSTEM_PROMPT_3 or LLM_SYSTEM_PROMPT or DEFAULT_SYSTEM_PROMPT
    return LLM_SYSTEM_PROMPT or DEFAULT_SYSTEM_PROMPT


async def call_llm(messages: List[Dict[str, str]], max_tokens: int, mode: str) -> Optional[str]:
    if not (LLM_ENABLED and openai_client):
        return None

    try:
        response = await openai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=LLM_TEMPERATURE,
            top_p=LLM_TOP_P,
            frequency_penalty=LLM_FREQUENCY_PENALTY,
            presence_penalty=LLM_PRESENCE_PENALTY,
            seed=int(LLM_SEED) if LLM_SEED is not None else None,
        )
        usage = getattr(response, "usage", None)
        if usage:
            logging.info(
                "OpenAI usage mode=%s prompt=%s completion=%s total=%s temperature=%s top_p=%s frequency_penalty=%s presence_penalty=%s seed=%s",
                mode,
                getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None),
                getattr(usage, "total_tokens", None),
                LLM_TEMPERATURE,
                LLM_TOP_P,
                LLM_FREQUENCY_PENALTY,
                LLM_PRESENCE_PENALTY,
                LLM_SEED,
            )
        else:
            logging.info(
                "OpenAI usage missing mode=%s temperature=%s top_p=%s frequency_penalty=%s presence_penalty=%s seed=%s",
                mode,
                LLM_TEMPERATURE,
                LLM_TOP_P,
                LLM_FREQUENCY_PENALTY,
                LLM_PRESENCE_PENALTY,
                LLM_SEED,
            )
        return response.choices[0].message.content if response.choices else None
    except Exception as exc:  # noqa: BLE001
        logging.warning("Не удалось получить ответ от LLM: %s", exc)
        return None


async def send_rendered_message(
    message: Message, text: str, reply_markup: Optional[ReplyKeyboardMarkup | InlineKeyboardMarkup] = None
) -> None:
    rendered = render_markers_to_html(text)
    try:
        await message.answer(rendered, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Не удалось отправить сообщение с HTML-разметкой: %s", exc)
        await message.answer(text, reply_markup=reply_markup)


async def generate_card_day_interpretation(card_name: str) -> str:
    fallback = f"[B]Карта дня:[/B] {card_name}. Интерпретация будет добавлена позже."
    messages = [
        {"role": "system", "content": get_system_prompt("DAY")},
        {
            "role": "user",
            "content": (
                "Контекст: Карта дня. Название карты: "
                f"{card_name}. Используй маркеры [B]...[/B] для выделения ключевого вывода. "
                "Не используй HTML."
            ),
        },
    ]

    text = await call_llm(messages=messages, max_tokens=LLM_MAX_TOKENS_DAY, mode="DAY")
    return text or fallback


async def generate_three_cards_interpretation(question: str, card_names: List[str]) -> str:
    joined_cards = ", ".join(card_names)
    messages = [
        {"role": "system", "content": get_system_prompt("THREE")},
        {
            "role": "user",
            "content": (
                f"Вопрос пользователя: {question}\n"
                f"Карты: {joined_cards}."
                " Опиши значение каждой карты и общий итог."
                " Используй маркеры [B]...[/B] для выделения ключевых выводов. "
                "Не используй HTML."
            ),
        },
    ]

    fallback = "[B]Интерпретация недоступна.[/B] Позже добавим подробности по раскладу."
    text = await call_llm(messages=messages, max_tokens=LLM_MAX_TOKENS_3, mode="THREE")
    return text or fallback


async def generate_clarify_interpretation(card_name: str, question: str) -> str:
    messages = [
        {"role": "system", "content": get_system_prompt("DAY")},
        {
            "role": "user",
            "content": (
                "Контекст: уточняющий вопрос по карте дня.\n"
                f"Карта: {card_name}.\n"
                f"Вопрос: {question}.\n"
                "Используй маркеры [B]...[/B] для выделения ключевых выводов. Не используй HTML."
            ),
        },
    ]
    fallback = "[B]Уточнение временно недоступно.[/B] Попробуйте позже."
    text = await call_llm(messages=messages, max_tokens=LLM_MAX_TOKENS_DAY, mode="DAY")
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
        "Привет, меня зовут Таро Жабка 🐸\n"
        "Если тебе что-то не даёт покоя — давай сделаем расклад и посмотрим, в чём дело.\n"
        "Со мной ты можешь разобрать любую тему и получить ясный ответ.",
        reply_markup=build_start_journey_keyboard(),
    )


@router.message(F.text.in_({"Меню", "⬅️ В меню"}))
async def handle_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_user_record(message.from_user.id)
    spreads_left = user.get("spreads_left", 0)
    diamonds = user.get("diamonds", 0)
    on_cooldown, remaining = is_on_cooldown(user.get("last_daily_spread_at"), DAILY_SPREAD_COOLDOWN)
    daily_status = f"через {format_remaining(remaining)}" if on_cooldown else "доступен"

    await message.answer(
        f"Доступно раскладов: {spreads_left}\nАлмазики: {diamonds}💎\nКарта дня: {daily_status}",
        reply_markup=build_menu_keyboard(),
    )


@router.message(F.text.in_({"Профиль", "⚙️ Профиль"}))
async def handle_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_user_record(message.from_user.id)
    await send_rendered_message(
        message,
        format_profile_text(user),
        reply_markup=build_menu_keyboard(),
    )


@router.callback_query(F.data == "start_journey")
async def handle_start_journey(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=build_menu_keyboard(),
    )


@router.callback_query(F.data == "spread_menu")
async def handle_spread_menu_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Тут ты можешь получить расклад.",
        reply_markup=build_spread_inline_keyboard(),
    )


@router.message(F.text.in_({"Получить расклад", "🔮 Получить расклад"}))
async def handle_get_spread(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Тут ты можешь получить расклад.",
        reply_markup=build_spread_inline_keyboard(),
    )


@router.callback_query(F.data == "spread_daily")
async def handle_spread_daily_inline(callback: CallbackQuery) -> None:
    await callback.answer()
    await trigger_daily_spread(callback.from_user.id, callback.message)


@router.callback_query(F.data == "spread_advanced")
async def handle_spread_advanced_inline(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Выберите направление:", reply_markup=build_advanced_categories_keyboard())


@router.callback_query(F.data == "spread_back")
async def handle_spread_back(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Возвращаю в меню.",
        reply_markup=build_menu_keyboard(),
    )


async def process_card_of_day(message: Message, user: Dict[str, Any], card_files: List[Path]) -> None:
    card_path = random.choice(card_files)
    await message.answer_photo(FSInputFile(card_path))
    interpretation = await generate_card_day_interpretation(card_path.stem)
    await send_rendered_message(message, interpretation, reply_markup=build_clarify_keyboard())
    user["last_daily_spread_at"] = now_utc().isoformat()
    user["daily_spread_count"] = user.get("daily_spread_count", 0) + 1
    user["last_daily_card"] = card_path.stem
    save_user_record(message.from_user.id, user)


async def trigger_daily_spread(user_id: int, message: Message) -> None:
    user = get_user_record(user_id)
    card_files = load_card_files()
    if not card_files:
        await message.answer(
            "Нет карт в базе, добавьте изображения в assets/cards.",
            reply_markup=build_menu_keyboard(),
        )
        return

    on_cooldown, remaining = is_on_cooldown(user.get("last_daily_spread_at"), DAILY_SPREAD_COOLDOWN)
    if on_cooldown:
        await message.answer(
            f"Расклад дня будет доступен через {format_remaining(remaining)}.",
            reply_markup=build_menu_keyboard(),
        )
        return

    await process_card_of_day(message, user, card_files)


@router.message(F.text.in_({"🃏 Расклад дня", "Карта дня"}))
async def handle_daily_spread(message: Message, state: FSMContext) -> None:
    await state.clear()
    await trigger_daily_spread(message.from_user.id, message)


@router.message(F.text == "🗝️ Продвинутые расклады")
async def handle_advanced_entry(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Выберите направление:", reply_markup=build_advanced_categories_keyboard())


@router.message(F.text == "Расклад из 3 карт")
async def handle_advanced_spread_choice(message: Message, state: FSMContext) -> None:
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


@router.message(F.text.in_({"🎁 Подарок", "Получить 💎"}))
async def handle_daily_gift(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_user_record(message.from_user.id)
    on_cooldown, remaining = is_on_cooldown(user.get("last_daily_gift_at"), DAILY_GIFT_COOLDOWN)
    if on_cooldown:
        await message.answer(
            f"Подарок будет доступен через {format_remaining(remaining)}.",
            reply_markup=build_menu_keyboard(),
        )
        return

    await send_rendered_message(
        message,
        "[B]🐸 Ежедневный подарок от Жабки[/B]\n"
        "Раз в 24 часа Жабка даёт тебе небольшой бонус.\n"
        "❌ Не совпало — Жабка даёт 5 кристалликов\n"
        "🎰 Три одинаковых — Жабка даёт 15 кристалликов\n"
        "💎 Джекпот — Жабка даёт 30 кристалликов",
        reply_markup=build_gift_inline_keyboard(),
    )


@router.callback_query(F.data == "roll_daily_gift")
async def handle_roll_daily_gift(callback: CallbackQuery) -> None:
    await callback.answer()
    user = get_user_record(callback.from_user.id)
    on_cooldown, remaining = is_on_cooldown(user.get("last_daily_gift_at"), DAILY_GIFT_COOLDOWN)
    if on_cooldown:
        await callback.message.answer(
            f"Подарок будет доступен через {format_remaining(remaining)}.",
            reply_markup=build_menu_keyboard(),
        )
        return

    dice_msg = await callback.message.answer_dice(emoji="🎰")
    dice_value = dice_msg.dice.value if dice_msg.dice else 0
    reward, _ = evaluate_slot_reward(dice_value)

    user["diamonds"] = user.get("diamonds", 0) + reward
    user["last_daily_gift_at"] = now_utc().isoformat()
    save_user_record(callback.from_user.id, user)

    await callback.message.answer(
        f"Вы выиграли {reward}💎!\nТеперь у тебя {user['diamonds']}💎",
        reply_markup=build_menu_keyboard(),
    )


def build_leaf_mapping() -> Dict[str, Dict[str, List[str]]]:
    return {
        "rel": {"title": "Расклады на отношения", "options": RELATION_SPREADS},
        "fin": {"title": "Расклады на финансы", "options": FINANCE_SPREADS},
        "self": {"title": "Расклады про себя", "options": SELF_SPREADS},
    }


@router.callback_query(F.data == "adv_relations")
async def handle_adv_relations(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Расклады на отношения",
        reply_markup=build_leaf_keyboard(RELATION_SPREADS, "rel"),
    )


@router.callback_query(F.data == "adv_finance")
async def handle_adv_finance(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Расклады на финансы",
        reply_markup=build_leaf_keyboard(FINANCE_SPREADS, "fin"),
    )


@router.callback_query(F.data == "adv_self")
async def handle_adv_self(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Расклады на день",
        reply_markup=build_leaf_keyboard(SELF_SPREADS, "self"),
    )


@router.callback_query(lambda c: ":" in c.data and c.data.split(":", 1)[0] in {"rel", "fin", "self"})
async def handle_leaf_stub(callback: CallbackQuery) -> None:
    await callback.answer()
    prefix, idx_str = callback.data.split(":", 1)
    mapping = build_leaf_mapping()
    leaf = mapping.get(prefix)
    if not leaf:
        return
    options = leaf["options"]
    try:
        idx = int(idx_str)
    except ValueError:
        return
    if idx < 0 or idx >= len(options):
        return
    choice = options[idx]
    await callback.message.answer(
        f"Заглушка: «{choice}». Скоро добавим интерпретацию.",
        reply_markup=build_advanced_categories_keyboard(),
    )


@router.message(F.text == "Уточняющий вопрос 10💎")
async def handle_clarify_request(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_user_record(message.from_user.id)
    card_name = user.get("last_daily_card")
    if not card_name:
        await message.answer("Сначала получите расклад дня.", reply_markup=build_menu_keyboard())
        return
    diamonds = user.get("diamonds", 0)
    if diamonds < CLARIFY_COST:
        await message.answer(
            f"Недостаточно алмазиков: {diamonds}💎. Нужно {CLARIFY_COST}💎.",
            reply_markup=build_menu_keyboard(),
        )
        return

    await state.set_state(SpreadStates.waiting_for_clarify)
    await state.update_data(card_name=card_name)
    await message.answer(
        f"Напишите уточняющий вопрос одним сообщением. Стоимость {CLARIFY_COST}💎 будет списана после ответа.",
        reply_markup=build_cancel_keyboard(),
    )


@router.message(SpreadStates.waiting_for_question, F.text == "Отмена")
async def handle_cancel_question(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=build_menu_keyboard())


@router.message(SpreadStates.waiting_for_clarify, F.text == "Отмена")
async def handle_cancel_clarify(message: Message, state: FSMContext) -> None:
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
    await send_rendered_message(message, interpretation, reply_markup=build_menu_keyboard())

    user["spreads_left"] = max(spreads_left - 1, 0)
    save_user_record(message.from_user.id, user)
    await state.clear()


@router.message(SpreadStates.waiting_for_clarify)
async def handle_clarify_question(message: Message, state: FSMContext) -> None:
    user = get_user_record(message.from_user.id)
    diamonds = user.get("diamonds", 0)
    if diamonds < CLARIFY_COST:
        await state.clear()
        await message.answer(
            f"Недостаточно алмазиков: {diamonds}💎. Нужно {CLARIFY_COST}💎.",
            reply_markup=build_menu_keyboard(),
        )
        return

    data = await state.get_data()
    card_name = data.get("card_name") or user.get("last_daily_card")
    if not card_name:
        await state.clear()
        await message.answer("Карта дня не найдена. Сначала получите расклад дня.", reply_markup=build_menu_keyboard())
        return

    question_text = message.text or ""
    interpretation = await generate_clarify_interpretation(card_name, question_text)
    user["diamonds"] = max(0, diamonds - CLARIFY_COST)
    save_user_record(message.from_user.id, user)
    await send_rendered_message(message, interpretation, reply_markup=build_menu_keyboard())
    await state.clear()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.info(
        "LLM params temperature=%s top_p=%s frequency_penalty=%s presence_penalty=%s seed=%s",
        LLM_TEMPERATURE,
        LLM_TOP_P,
        LLM_FREQUENCY_PENALTY,
        LLM_PRESENCE_PENALTY,
        LLM_SEED,
    )
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
