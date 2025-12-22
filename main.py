import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aiogram import BaseMiddleware, Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    KeyboardButton,
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
from prompts import DEFAULT_SYSTEM_PROMPT, PROMPT_REGISTRY, build_prompt_messages

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_ENABLED = os.getenv("LLM_ENABLED", "1") == "1"
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1-mini")
LLM_MAX_TOKENS_DAY = int(os.getenv("LLM_MAX_TOKENS_DAY", "220"))
LLM_MAX_TOKENS_3 = int(os.getenv("LLM_MAX_TOKENS_3", "420"))
LLM_SYSTEM_PROMPT = os.getenv("LLM_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)
LLM_SYSTEM_PROMPT_DAY = os.getenv("LLM_SYSTEM_PROMPT_DAY")
LLM_SYSTEM_PROMPT_3 = os.getenv("LLM_SYSTEM_PROMPT_3")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.4"))
LLM_TOP_P = float(os.getenv("LLM_TOP_P", "1.0"))
LLM_FREQUENCY_PENALTY = float(os.getenv("LLM_FREQUENCY_PENALTY", "0.2"))
LLM_PRESENCE_PENALTY = float(os.getenv("LLM_PRESENCE_PENALTY", "0.0"))
LLM_SEED = os.getenv("LLM_SEED")
DAILY_GIFT_COOLDOWN = timedelta(hours=24)
CLARIFY_COST = 10
DATA_FILE = Path("data/users.json")
CARDS_DIR = Path("assets/cards")
CARD_EXTENSIONS = {".png", ".jpg", ".jpeg"}
THREE_CARD_SPREAD_COST = 5
DAILY_SPREAD_COST = 5
INVITE_DIAMOND_REWARD = 10
SUBSCRIPTION_DIAMOND_REWARD = 10
SUBSCRIPTION_REQUIRED_FLAG = "requires_subscription"
DEFAULT_USER = {
    "free_granted": False,
    "invited_count": 0,
    "referred_by": None,
    "registration_date": None,
    "diamonds": 0,
    "last_daily_spread_at": None,
    "last_daily_gift_at": None,
    "daily_spread_count": 0,
    "last_daily_card": None,
    "subscription_status": None,
    "subscription_checked_at": None,
}
RELATION_OPTIONS: List[Tuple[str, str]] = [
    (f"Есть ли у него другая? {THREE_CARD_SPREAD_COST}💎", "REL_HAS_OTHER"),
    (f"Изменял ли он мне? {THREE_CARD_SPREAD_COST}💎", "REL_IS_CHEATING"),
    (f"Любит ли он меня на самом деле? {THREE_CARD_SPREAD_COST}💎", "REL_TRUE_LOVE"),
    (f"Считает ли он меня «своей женщиной»? {THREE_CARD_SPREAD_COST}💎", "REL_OWN_WOMAN"),
    (f"Уйдёт ли он от меня? {THREE_CARD_SPREAD_COST}💎", "REL_LEAVE_ME"),
]
FINANCE_OPTIONS: List[Tuple[str, str]] = [
    (f"Будут ли у меня деньги в ближайшее время? {THREE_CARD_SPREAD_COST}💎", "FIN_SOON_MONEY"),
    (f"Почему деньги не задерживаются? {THREE_CARD_SPREAD_COST}💎", "FIN_NO_STICK"),
    (f"Тратить на себя или экономить? {THREE_CARD_SPREAD_COST}💎", "FIN_SPEND_OR_SAVE"),
    (f"Найду ли я того кто меня обеспечит? {THREE_CARD_SPREAD_COST}💎", "FIN_FIND_SPONSOR"),
]
SELF_OPTIONS: List[Tuple[str, str]] = [
    (f"Где ты врёшь себе {THREE_CARD_SPREAD_COST}💎", "SELF_LIE"),
    (f"Что тебя реально сдерживает {THREE_CARD_SPREAD_COST}💎", "SELF_BLOCKS"),
    (f"Чего ты на самом деле хочешь {THREE_CARD_SPREAD_COST}💎", "SELF_WANT"),
    (f"В чём твой внутренний конфликт {THREE_CARD_SPREAD_COST}💎", "SELF_CONFLICT"),
    (f"Какую роль ты сейчас играешь {THREE_CARD_SPREAD_COST}💎", "SELF_ROLE"),
]
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


def subscription_required(handler: Any) -> Any:
    setattr(handler, SUBSCRIPTION_REQUIRED_FLAG, True)
    callback = getattr(handler, "callback", None)
    if callback:
        setattr(callback, SUBSCRIPTION_REQUIRED_FLAG, True)
    return handler


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
    builder.button(text="✨ Получить расклад ✨")
    builder.button(text="🚀 Премиум")
    builder.button(text="👤 Профиль")
    builder.button(text="🔥 Бесплатные расклады")
    builder.button(text="🏛 Испытай судьбу")
    builder.adjust(2, 2, 1)
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
    builder.button(text=f"🃏 Расклад дня {DAILY_SPREAD_COST}💎", callback_data="spread_daily")
    builder.button(text=f"🗝️ Продвинутые расклады {THREE_CARD_SPREAD_COST}💎", callback_data="spread_advanced")
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


def build_diamonds_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎁Подарок")
    builder.button(text="Пригласить друзей")
    builder.button(text="Купить💎")
    builder.button(text="⬅️Назад")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)


def build_leaf_keyboard(options: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for text, key in options:
        builder.button(text=text, callback_data=f"leaf:{key}")
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
    triple_values = {1, 22, 43, 64}
    if value == 64:
        return 30, "💎 Джекпот — Жабка даёт 30 кристалликов"
    if value in triple_values:
        return 15, "🎰 Три одинаковых — Жабка даёт 15 кристалликов"
    return 5, "❌ Не совпало — Жабка даёт 5 кристалликов"


async def ensure_subscribed(bot: Bot, user_id: int, message_or_callback: Message | CallbackQuery) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        status = member.status
    except Exception as exc:  # noqa: BLE001
        logging.warning("Не удалось проверить подписку: %s", exc)
        status = None

    if status is not None:
        user = get_user_record(user_id)
        user["subscription_status"] = status
        user["subscription_checked_at"] = now_utc().isoformat()
        if status not in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED} and not user.get("free_granted"):
            user["diamonds"] = user.get("diamonds", 0) + SUBSCRIPTION_DIAMOND_REWARD
            user["free_granted"] = True
        save_user_record(user_id, user)

    is_callback = isinstance(message_or_callback, CallbackQuery) or hasattr(message_or_callback, "message")

    if status is None:
        text = "Не удалось проверить подписку. Попробуйте ещё раз."
        if is_callback:
            await message_or_callback.answer(text)
            message_object = getattr(message_or_callback, "message", None)
            if message_object:
                await message_or_callback.message.answer(
                    "Для использования бота подпишитесь на канал",
                    reply_markup=build_subscription_keyboard(),
                )
        else:
            await message_or_callback.answer(text, reply_markup=build_subscription_keyboard())
        return False

    if status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        text = "Для использования бота подпишитесь на канал"
        keyboard = build_subscription_keyboard()
        if is_callback:
            await message_or_callback.answer("Подпишитесь на канал, чтобы продолжить.")
            message_object = getattr(message_or_callback, "message", None)
            if message_object:
                await message_object.answer(text, reply_markup=keyboard)
        else:
            await message_or_callback.answer(text, reply_markup=keyboard)
        return False

    if is_callback:
        try:
            await message_or_callback.answer()
        except Exception as exc:  # noqa: BLE001
            logging.info("Не удалось отправить ответ на callback: %s", exc)
    return True


class SubscriptionMiddleware(BaseMiddleware):
    def __init__(self, exempt_handlers: Optional[set[str]] = None) -> None:
        self.exempt_handlers = exempt_handlers or set()
        super().__init__()

    async def __call__(self, handler: Any, event: Any, data: Dict[str, Any]) -> Any:
        clean_data = dict(data)
        clean_data.pop("dispatcher", None)
        clean_data.pop("bots", None)
        target = getattr(handler, "callback", handler)
        handler_name = getattr(target, "__name__", "")
        is_protected = getattr(target, SUBSCRIPTION_REQUIRED_FLAG, True)
        if handler_name in self.exempt_handlers or not is_protected:
            return await handler(event, clean_data)

        user = getattr(event, "from_user", None)
        bot = clean_data.get("bot")
        if not bot or not user:
            return await handler(event, clean_data)

        is_subscribed = await ensure_subscribed(bot, user.id, event)
        if not is_subscribed:
            return None

        return await handler(event, clean_data)


def format_profile_text(user: Dict[str, Any]) -> str:
    reg_dt = iso_to_datetime(user.get("registration_date"))
    reg_str = reg_dt.strftime("%Y-%m-%d %H:%M UTC") if reg_dt else "неизвестно"
    diamonds = user.get("diamonds", 0)
    invited = user.get("invited_count", 0)
    last_daily_card = user.get("last_daily_card")
    daily_card_text = last_daily_card or "ещё не было"
    return (
        "[B]Профиль[/B]\n"
        f"Дата регистрации: {reg_str}\n"
        f"Алмазики: {diamonds}💎\n"
        f"Приглашённых друзей: {invited}\n"
        f"Последняя карта дня: {daily_card_text}"
    )


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
    messages = build_prompt_messages(
        "card_day",
        base_prompt=LLM_SYSTEM_PROMPT,
        day_prompt=LLM_SYSTEM_PROMPT_DAY,
        three_prompt=LLM_SYSTEM_PROMPT_3,
        card_name=card_name,
    )

    text = await call_llm(messages=messages, max_tokens=LLM_MAX_TOKENS_DAY, mode="DAY")
    return text or fallback


async def generate_prompt_interpretation(prompt_key: str, question: str = "", card_names: List[str] | None = None) -> str:
    card_names = card_names or []
    joined_cards = ", ".join(card_names)
    safe_question = question or ""
    config = PROMPT_REGISTRY.get(prompt_key)
    mode = config.mode if config else "THREE"
    messages = build_prompt_messages(
        prompt_key,
        base_prompt=LLM_SYSTEM_PROMPT,
        day_prompt=LLM_SYSTEM_PROMPT_DAY,
        three_prompt=LLM_SYSTEM_PROMPT_3,
        question=safe_question,
        cards=joined_cards,
    )
    fallback = "[B]Интерпретация недоступна.[/B] Позже добавим подробности по раскладу."
    max_tokens = LLM_MAX_TOKENS_DAY if mode == "DAY" else LLM_MAX_TOKENS_3
    text = await call_llm(messages=messages, max_tokens=max_tokens, mode=mode)
    return text or fallback


async def generate_clarify_interpretation(card_name: str, question: str) -> str:
    messages = build_prompt_messages(
        "clarify",
        base_prompt=LLM_SYSTEM_PROMPT,
        day_prompt=LLM_SYSTEM_PROMPT_DAY,
        three_prompt=LLM_SYSTEM_PROMPT_3,
        card_name=card_name,
        question=question,
    )
    fallback = "[B]Уточнение временно недоступно.[/B] Попробуйте позже."
    text = await call_llm(messages=messages, max_tokens=LLM_MAX_TOKENS_DAY, mode="DAY")
    return text or fallback


async def process_prompt_spread(message: Message, prompt_key: str, question: str = "") -> bool:
    user = get_user_record(message.from_user.id)
    diamonds = user.get("diamonds", 0)
    if diamonds < THREE_CARD_SPREAD_COST:
        await message.answer(
            f"Недостаточно алмазиков: {diamonds}💎. Нужно {THREE_CARD_SPREAD_COST}💎.",
            reply_markup=build_diamonds_keyboard(),
        )
        return False

    card_files = load_card_files()
    if len(card_files) < 3:
        await message.answer(
            "Недостаточно карт в базе, добавьте не менее 3 изображений в assets/cards.",
            reply_markup=build_menu_keyboard(),
        )
        return False

    selected_cards = random.sample(card_files, 3)
    collage_file = create_three_card_collage(selected_cards)
    await message.answer_photo(collage_file)

    card_names = [card.stem for card in selected_cards]
    card_names_text = "Выпали карты: " + ", ".join(card_names)
    await message.answer(card_names_text)
    interpretation = await generate_prompt_interpretation(prompt_key, question=question, card_names=card_names)
    await send_rendered_message(message, interpretation, reply_markup=build_menu_keyboard())

    user["diamonds"] = max(0, diamonds - THREE_CARD_SPREAD_COST)
    save_user_record(message.from_user.id, user)
    return True


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
            inviter_record["diamonds"] = inviter_record.get("diamonds", 0) + INVITE_DIAMOND_REWARD
            inviter_record["invited_count"] += 1
            users[inviter_key] = inviter_record
            new_user_record["referred_by"] = referral_payload
            try:
                await bot.send_message(
                    referral_payload,
                    f"Вам начислено {INVITE_DIAMOND_REWARD}💎 за приглашенного друга. "
                    f"Доступно {inviter_record['diamonds']}💎",
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

    subscribed = await ensure_subscribed(bot, user_id, message)
    if not subscribed:
        return

    await message.answer(
        "Привет, меня зовут Таро Жабка 🐸\n"
        "Если тебе что-то не даёт покоя — давай сделаем расклад и посмотрим, в чём дело.\n"
        "Со мной ты можешь разобрать любую тему и получить ясный ответ.",
        reply_markup=build_start_journey_keyboard(),
    )


@router.callback_query(lambda c: c.data == "check_subscription")
async def handle_check_subscription(callback: CallbackQuery, bot: Bot) -> None:
    subscribed = await ensure_subscribed(bot, callback.from_user.id, callback)
    if not subscribed:
        return

    await callback.message.answer(
        "Привет, меня зовут Таро Жабка 🐸\n"
        "Если тебе что-то не даёт покоя — давай сделаем расклад и посмотрим, в чём дело.\n"
        "Со мной ты можешь разобрать любую тему и получить ясный ответ.",
        reply_markup=build_start_journey_keyboard(),
    )


@subscription_required
@router.message(F.text.in_({"Меню", "⬅️ В меню", "⬅️Назад"}))
async def handle_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_user_record(message.from_user.id)
    diamonds = user.get("diamonds", 0)

    await message.answer(
        f"Алмазики: {diamonds}💎\n"
        f"Расклад дня стоит {DAILY_SPREAD_COST}💎, расклады из 3 карт — {THREE_CARD_SPREAD_COST}💎.",
        reply_markup=build_menu_keyboard(),
    )


@subscription_required
@router.message(F.text.in_({"Профиль", "⚙️ Профиль", "👤 Профиль"}))
async def handle_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_user_record(message.from_user.id)
    await send_rendered_message(
        message,
        format_profile_text(user),
        reply_markup=build_menu_keyboard(),
    )


@subscription_required
@router.callback_query(F.data == "start_journey")
async def handle_start_journey(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=build_menu_keyboard(),
    )


@subscription_required
@router.callback_query(F.data == "spread_menu")
async def handle_spread_menu_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        f"Тут ты можешь получить расклад. Стоимость: карта дня — {DAILY_SPREAD_COST}💎, расклады из 3 карт — {THREE_CARD_SPREAD_COST}💎.",
        reply_markup=build_spread_inline_keyboard(),
    )


@subscription_required
@router.message(F.text.in_({"Получить расклад", "🔮 Получить расклад", "✨ Получить расклад"}))
async def handle_get_spread(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        f"Тут ты можешь получить расклад. Стоимость: карта дня — {DAILY_SPREAD_COST}💎, расклады из 3 карт — {THREE_CARD_SPREAD_COST}💎.",
        reply_markup=build_spread_inline_keyboard(),
    )


@subscription_required
@router.message(F.text == "🔥 Бесплатные расклады")
async def handle_free_spreads(message: Message, state: FSMContext) -> None:
    await handle_get_spread(message, state)


@subscription_required
@router.callback_query(F.data == "spread_daily")
async def handle_spread_daily_inline(callback: CallbackQuery) -> None:
    await callback.answer()
    await trigger_daily_spread(callback.from_user.id, callback.message)


@subscription_required
@router.callback_query(F.data == "spread_advanced")
async def handle_spread_advanced_inline(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        f"Выберите направление (стоимость {THREE_CARD_SPREAD_COST}💎):",
        reply_markup=build_advanced_categories_keyboard(),
    )


@subscription_required
@router.callback_query(F.data == "spread_back")
async def handle_spread_back(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Возвращаю в меню.",
        reply_markup=build_menu_keyboard(),
    )


@subscription_required
@router.message(F.text == "Получить 💎")
async def handle_get_diamonds(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "У нас много способов для получения кристаликов:\n"
        "Приглашай друзей и получай по 10 за каждого\n"
        "Каждый день тебе доступен бесплатный подарок от Жабки\n"
        "А так же можешь купить недостающие алмазики.",
        reply_markup=build_diamonds_keyboard(),
    )


async def process_card_of_day(
    message: Message, user: Dict[str, Any], card_files: List[Path], *, cost: int
) -> None:
    card_path = random.choice(card_files)
    await message.answer_photo(FSInputFile(card_path))
    interpretation = await generate_card_day_interpretation(card_path.stem)
    await send_rendered_message(message, interpretation, reply_markup=build_clarify_keyboard())
    user["last_daily_spread_at"] = now_utc().isoformat()
    user["daily_spread_count"] = user.get("daily_spread_count", 0) + 1
    user["last_daily_card"] = card_path.stem
    user["diamonds"] = max(0, user.get("diamonds", 0) - cost)
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

    diamonds = user.get("diamonds", 0)
    if diamonds < DAILY_SPREAD_COST:
        await message.answer(
            f"Недостаточно алмазиков: {diamonds}💎. Нужно {DAILY_SPREAD_COST}💎 для карты дня.",
            reply_markup=build_diamonds_keyboard(),
        )
        return

    await process_card_of_day(message, user, card_files, cost=DAILY_SPREAD_COST)


@subscription_required
@router.message(F.text.in_({"🃏 Расклад дня", "Карта дня"}))
async def handle_daily_spread(message: Message, state: FSMContext) -> None:
    await state.clear()
    await trigger_daily_spread(message.from_user.id, message)


@subscription_required
@router.message(F.text == "🗝️ Продвинутые расклады")
async def handle_advanced_entry(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        f"Выберите направление (стоимость {THREE_CARD_SPREAD_COST}💎):",
        reply_markup=build_advanced_categories_keyboard(),
    )


@subscription_required
@router.message(F.text == "Расклад из 3 карт")
async def handle_advanced_spread_choice(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_user_record(message.from_user.id)
    diamonds = user.get("diamonds", 0)
    if diamonds < THREE_CARD_SPREAD_COST:
        await message.answer(
            f"Недостаточно алмазиков: {diamonds}💎. Нужно {THREE_CARD_SPREAD_COST}💎 для расклада из 3 карт.",
            reply_markup=build_diamonds_keyboard(),
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


@subscription_required
@router.message(F.text.in_({"Premium", "🚀 Премиум"}))
async def handle_premium(message: Message) -> None:
    await message.answer("Premium скоро будет доступен.", reply_markup=build_menu_keyboard())


@subscription_required
@router.message(F.text == "Купить💎")
async def handle_buy_diamonds(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Скоро добавим покупку алмазиков. Пока доступны приглашения друзей и ежедневный подарок.",
        reply_markup=build_diamonds_keyboard(),
    )


@subscription_required
@router.message(F.text.in_({"Пригласить друга", "Пригласить друзей"}))
async def handle_invite_friend(message: Message, bot: Bot) -> None:
    me = await bot.get_me()
    bot_username = me.username
    if not bot_username:
        await message.answer("Не удалось получить имя бота для ссылки.", reply_markup=build_menu_keyboard())
        return

    referral_link = f"https://t.me/{bot_username}?start={message.from_user.id}"
    await message.answer(
        "Поделитесь ссылкой с другом, чтобы получить дополнительный расклад:\n" f"{referral_link}",
        reply_markup=build_diamonds_keyboard(),
    )


@subscription_required
@router.message(F.text.in_({"🎁 Подарок", "🎁Подарок", "🏛 Испытай судьбу"}))
async def handle_daily_gift(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = get_user_record(message.from_user.id)
    on_cooldown, remaining = is_on_cooldown(user.get("last_daily_gift_at"), DAILY_GIFT_COOLDOWN)
    if on_cooldown:
        await message.answer(
            f"Подарок будет доступен через {format_remaining(remaining)}.",
            reply_markup=build_diamonds_keyboard(),
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


@subscription_required
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


@subscription_required
@router.callback_query(F.data == "adv_relations")
async def handle_adv_relations(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Расклады на отношения",
        reply_markup=build_leaf_keyboard(RELATION_OPTIONS),
    )


@subscription_required
@router.callback_query(F.data == "adv_finance")
async def handle_adv_finance(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Расклады на финансы",
        reply_markup=build_leaf_keyboard(FINANCE_OPTIONS),
    )


@subscription_required
@router.callback_query(F.data == "adv_self")
async def handle_adv_self(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Расклады про себя",
        reply_markup=build_leaf_keyboard(SELF_OPTIONS),
    )


@subscription_required
@router.callback_query(F.data.startswith("leaf:"))
async def handle_leaf_selection(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    prompt_key = callback.data.split(":", 1)[1]
    await state.clear()
    if not callback.message:
        return
    await process_prompt_spread(callback.message, prompt_key, question="")


@subscription_required
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


@subscription_required
@router.message(SpreadStates.waiting_for_question, F.text == "Отмена")
async def handle_cancel_question(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=build_menu_keyboard())


@subscription_required
@router.message(SpreadStates.waiting_for_clarify, F.text == "Отмена")
async def handle_cancel_clarify(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=build_menu_keyboard())


@subscription_required
@router.message(SpreadStates.waiting_for_question)
async def handle_three_card_question(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_key = data.get("prompt_key", "three_cards")
    question_text = message.text or ""
    await process_prompt_spread(message, prompt_key, question=question_text)
    await state.clear()


@subscription_required
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
    subscription_middleware = SubscriptionMiddleware(
        exempt_handlers={"handle_start", "handle_check_subscription"}
    )
    dispatcher.message.middleware(subscription_middleware)
    dispatcher.callback_query.middleware(subscription_middleware)
    dispatcher.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
