import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv(".env.spreads", override=True)

DEFAULT_SYSTEM_PROMPT = (
    "Ты помогаешь кратко и нейтрально интерпретировать карты Таро. "
    "Отвечай на русском языке без мистики и пафоса, лаконично и спокойно."
)


@dataclass
class PromptConfig:
    key: str
    mode: str  # e.g., DAY, THREE
    user_template: str


PROMPT_REGISTRY: Dict[str, PromptConfig] = {
    "card_day": PromptConfig(
        key="card_day",
        mode="DAY",
        user_template=(
            "Контекст: Карта дня. Название карты: {card_name}. "
            "Используй маркеры [B]...[/B] для выделения ключевого вывода. "
            "Не используй HTML."
        ),
    ),
    "three_cards": PromptConfig(
        key="three_cards",
        mode="THREE",
        user_template=(
            "Вопрос пользователя: {question}\n"
            "Карты: {cards}. Опиши значение каждой карты и общий итог. "
            "Используй маркеры [B]...[/B] для выделения ключевых выводов. "
            "Не используй HTML."
        ),
    ),
    "clarify": PromptConfig(
        key="clarify",
        mode="DAY",
        user_template=(
            "Контекст: уточняющий вопрос по карте дня.\n"
            "Карта: {card_name}.\n"
            "Вопрос: {question}.\n"
            "Используй маркеры [B]...[/B] для выделения ключевых выводов. Не используй HTML."
        ),
    ),
    "REL_HAS_OTHER": PromptConfig(
        key="REL_HAS_OTHER",
        mode="THREE",
        user_template=(
            "Тема: отношения. Контекст: выяснить, есть ли у партнёра другая.\n"
            "Карты: {cards}. Дай краткий разбор и общий вывод. "
            "Используй [B]...[/B] для ключевых тезисов. Не используй HTML."
        ),
    ),
    "REL_IS_CHEATING": PromptConfig(
        key="REL_IS_CHEATING",
        mode="THREE",
        user_template=(
            "Тема: отношения. Контекст: понять, изменял ли партнёр.\n"
            "Карты: {cards}. Ответь, изменял ли партнёр, выдели кратко выводы. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "REL_TRUE_LOVE": PromptConfig(
        key="REL_TRUE_LOVE",
        mode="THREE",
        user_template=(
            "Тема: отношения. Контекст: определить, любит ли он на самом деле.\n"
            "Карты: {cards}. Определи, любит ли он на самом деле, дай общий вывод. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "REL_OWN_WOMAN": PromptConfig(
        key="REL_OWN_WOMAN",
        mode="THREE",
        user_template=(
            "Тема: отношения. Контекст: считает ли он меня своей женщиной.\n"
            "Карты: {cards}. Ответь, считает ли он меня своей женщиной, дай общий вывод. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "REL_LEAVE_ME": PromptConfig(
        key="REL_LEAVE_ME",
        mode="THREE",
        user_template=(
            "Тема: отношения. Контекст: уйдёт ли он от меня.\n"
            "Карты: {cards}. Ответь, уйдёт ли он от меня, выдели ключевые выводы. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "FIN_SOON_MONEY": PromptConfig(
        key="FIN_SOON_MONEY",
        mode="THREE",
        user_template=(
            "Тема: финансы. Контекст: будут ли деньги в ближайшее время.\n"
            "Карты: {cards}. Ответь, будут ли деньги в ближайшее время, дай общий вывод. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "FIN_NO_STICK": PromptConfig(
        key="FIN_NO_STICK",
        mode="THREE",
        user_template=(
            "Тема: финансы. Контекст: почему деньги не задерживаются.\n"
            "Карты: {cards}. Объясни, почему деньги не задерживаются, и дай рекомендации. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "FIN_SPEND_OR_SAVE": PromptConfig(
        key="FIN_SPEND_OR_SAVE",
        mode="THREE",
        user_template=(
            "Тема: финансы. Контекст: тратить или экономить.\n"
            "Карты: {cards}. Сравни тратить или экономить, дай общий вывод. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "FIN_FIND_SPONSOR": PromptConfig(
        key="FIN_FIND_SPONSOR",
        mode="THREE",
        user_template=(
            "Тема: финансы. Контекст: найду ли я того, кто меня обеспечит.\n"
            "Карты: {cards}. Ответь, найду ли я того, кто меня обеспечит, дай краткий итог. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "SELF_LIE": PromptConfig(
        key="SELF_LIE",
        mode="THREE",
        user_template=(
            "Тема: про себя. Контекст: где я себе лгу.\n"
            "Карты: {cards}. Подскажи, где я себе лгу, выдели ключевые выводы. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "SELF_BLOCKS": PromptConfig(
        key="SELF_BLOCKS",
        mode="THREE",
        user_template=(
            "Тема: про себя. Контекст: что меня реально сдерживает.\n"
            "Карты: {cards}. Что реально меня сдерживает? Дай краткий итог. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "SELF_WANT": PromptConfig(
        key="SELF_WANT",
        mode="THREE",
        user_template=(
            "Тема: про себя. Контекст: чего я на самом деле хочу.\n"
            "Карты: {cards}. Чего я на самом деле хочу? Выдели ключевые выводы. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "SELF_CONFLICT": PromptConfig(
        key="SELF_CONFLICT",
        mode="THREE",
        user_template=(
            "Тема: про себя. Контекст: в чём мой внутренний конфликт.\n"
            "Карты: {cards}. В чём мой внутренний конфликт? Дай общий вывод. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
    "SELF_ROLE": PromptConfig(
        key="SELF_ROLE",
        mode="THREE",
        user_template=(
            "Тема: про себя. Контекст: какую роль я сейчас играю.\n"
            "Карты: {cards}. Какую роль я сейчас играю? Подчеркни ключевые выводы. "
            "Используй [B]...[/B], не используй HTML."
        ),
    ),
}


def resolve_system_prompt(
    mode: str,
    base_prompt: Optional[str],
    day_prompt: Optional[str],
    three_prompt: Optional[str],
) -> str:
    fallback = base_prompt or DEFAULT_SYSTEM_PROMPT
    if mode == "DAY":
        return day_prompt or fallback
    if mode == "THREE":
        return three_prompt or fallback
    return fallback


def load_prompt_override(prompt_key: str) -> Optional[str]:
    env_value = os.getenv(f"SPREAD_PROMPT_{prompt_key.upper()}")
    if env_value:
        return env_value

    file_path = Path("prompts") / f"{prompt_key}.txt"
    if file_path.exists():
        try:
            return file_path.read_text(encoding="utf-8")
        except OSError:
            return None
    return None


def build_prompt_messages(
    prompt_key: str,
    *,
    base_prompt: Optional[str],
    day_prompt: Optional[str],
    three_prompt: Optional[str],
    **kwargs,
) -> List[Dict[str, str]]:
    config = PROMPT_REGISTRY.get(prompt_key)
    if not config:
        raise KeyError(f"Unknown prompt key: {prompt_key}")

    system_prompt = resolve_system_prompt(config.mode, base_prompt, day_prompt, three_prompt)
    override = load_prompt_override(prompt_key)
    kwargs.setdefault("question", "")
    kwargs.setdefault("cards", "")

    template = override or config.user_template
    if config.mode == "THREE" and "{cards}" not in template:
        logging.warning(
            "Template for prompt '%s' does not include {cards}. Falling back to default template.",
            prompt_key,
        )
        template = config.user_template

    user_text = template.format(**kwargs)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
