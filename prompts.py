from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

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
    user_text = override or config.user_template.format(**kwargs)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
