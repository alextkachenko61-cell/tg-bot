import os
import asyncio
import types

import pytest
from aiogram.enums import ChatMemberStatus

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("CHANNEL_USERNAME", "@test_channel")

import main  # noqa: E402


class DummyBot:
    def __init__(self, status: ChatMemberStatus) -> None:
        self.status = status
        self.calls = []

    async def get_chat_member(self, channel_username: str, user_id: int):
        self.calls.append((channel_username, user_id))
        return types.SimpleNamespace(status=self.status)


class DummyMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = types.SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text: str, reply_markup=None, parse_mode=None):
        self.answers.append(
            {"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode}
        )


class DummyCallback:
    def __init__(self, user_id: int) -> None:
        self.from_user = types.SimpleNamespace(id=user_id)
        self.message = DummyMessage(user_id)
        self.answers = []

    async def answer(self, text: str | None = None):
        self.answers.append(text)


@pytest.fixture(autouse=True)
def override_data_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_FILE", tmp_path / "users.json")
    yield


def test_ensure_subscribed_allows_member_and_rewards_once():
    bot = DummyBot(ChatMemberStatus.MEMBER)
    message = DummyMessage(user_id=1)

    is_subscribed = asyncio.run(main.ensure_subscribed(bot, 1, message))

    assert is_subscribed is True
    assert message.answers == []
    user = main.get_user_record(1)
    assert user["subscription_status"] == ChatMemberStatus.MEMBER
    assert user["free_granted"] is True
    assert user["diamonds"] == main.SUBSCRIPTION_DIAMOND_REWARD


def test_ensure_subscribed_blocks_and_notifies_for_left_user():
    bot = DummyBot(ChatMemberStatus.LEFT)
    message = DummyMessage(user_id=2)

    is_subscribed = asyncio.run(main.ensure_subscribed(bot, 2, message))

    assert is_subscribed is False
    assert message.answers
    assert "подпишитесь на канал" in message.answers[0]["text"].lower()
    user = main.get_user_record(2)
    assert user["subscription_status"] == ChatMemberStatus.LEFT


def test_middleware_blocks_protected_handler_for_unsubscribed_text():
    bot = DummyBot(ChatMemberStatus.LEFT)
    middleware = main.SubscriptionMiddleware()
    called = []

    @main.subscription_required
    async def protected_handler(event, **_):
        called.append(True)
        return "ok"

    message = DummyMessage(user_id=5)
    result = asyncio.run(middleware(protected_handler, message, {"bot": bot}))

    assert result is None
    assert not called
    assert message.answers
    assert "подпишитесь на канал" in message.answers[0]["text"].lower()


def test_ensure_subscribed_answers_callback_on_refusal():
    bot = DummyBot(ChatMemberStatus.KICKED)
    callback = DummyCallback(user_id=7)

    is_subscribed = asyncio.run(main.ensure_subscribed(bot, 7, callback))

    assert is_subscribed is False
    assert callback.answers
    assert "подпишитесь" in (callback.answers[0] or "").lower()
    assert callback.message.answers
