from typing import Optional

DEFAULT_SYSTEM_PROMPT = (
    "Ты помогаешь кратко и нейтрально интерпретировать карты Таро. "
    "Отвечай на русском языке без мистики и пафоса, лаконично и спокойно."
)


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


def build_card_day_user_prompt(card_name: str) -> str:
    return (
        "Контекст: Карта дня. Название карты: "
        f"{card_name}. Используй маркеры [B]...[/B] для выделения ключевого вывода. "
        "Не используй HTML."
    )


def build_three_cards_user_prompt(question: str, joined_cards: str) -> str:
    return (
        f"Вопрос пользователя: {question}\n"
        f"Карты: {joined_cards}."
        " Опиши значение каждой карты и общий итог."
        " Используй маркеры [B]...[/B] для выделения ключевых выводов. "
        "Не используй HTML."
    )


def build_clarify_user_prompt(card_name: str, question: str) -> str:
    return (
        "Контекст: уточняющий вопрос по карте дня.\n"
        f"Карта: {card_name}.\n"
        f"Вопрос: {question}.\n"
        "Используй маркеры [B]...[/B] для выделения ключевых выводов. Не используй HTML."
    )
