"""
tests/test_notify.py
=====================
Notification seam tests: Discord webhook embeds against a fake HTTP
session (never live network), backend fan-out, and the VOSS_NOTIFY
factory. Fully offline.
"""

from __future__ import annotations

import pytest

from pipeline.notify import (
    DiscordNotifier,
    LogNotifier,
    MultiNotifier,
    NotifierBackend,
    NotifyEvent,
    NotifyLevel,
    NullNotifier,
    _DISCORD_COLOR_MAP,
    get_notifier,
)


# ── Fakes ────────────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code=204):
        self.status_code = status_code


class FakeSession:
    """Captures the last POST url + json payload; never touches the network."""

    def __init__(self, status_code=204):
        self.calls = 0
        self.status_code = status_code
        self.last_url = None
        self.last_json = None
        self.last_timeout = None

    def post(self, url, json=None, timeout=None):
        self.calls += 1
        self.last_url = url
        self.last_json = json
        self.last_timeout = timeout
        return FakeResponse(self.status_code)


class RaisingSession:
    """Simulates a network failure: .post always raises."""

    def post(self, url, json=None, timeout=None):
        raise ConnectionError("boom")


def _event(level=NotifyLevel.INFO, **overrides):
    kwargs = dict(
        name="slate_published",
        level=level,
        title="Slate published",
        message="42 props ready",
        fields={"props": 42, "sport": "mlb"},
        sport="mlb",
    )
    kwargs.update(overrides)
    return NotifyEvent(**kwargs)


# ── DiscordNotifier ──────────────────────────────────────────────────────────────

class TestDiscordNotifier:

    def test_posts_one_embed_with_correct_fields(self):
        session = FakeSession()
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/abc", session=session)
        event = _event()

        result = notifier.send(event)

        assert result is True
        assert session.calls == 1
        assert session.last_url == "https://discord.com/api/webhooks/abc"

        payload = session.last_json
        assert len(payload["embeds"]) == 1
        embed = payload["embeds"][0]
        assert embed["title"] == "Slate published"
        assert embed["description"] == "42 props ready"
        assert embed["color"] == _DISCORD_COLOR_MAP[NotifyLevel.INFO]

        rendered = {f["name"]: f["value"] for f in embed["fields"]}
        assert rendered == {"props": "42", "sport": "mlb"}
        assert all(f["inline"] is True for f in embed["fields"])

        assert "slate_published" in embed["footer"]["text"]
        assert "mlb" in embed["footer"]["text"]

    def test_timeout_is_bounded(self):
        session = FakeSession()
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/abc", session=session)
        notifier.send(_event())
        assert session.last_timeout is not None
        assert session.last_timeout <= 10

    @pytest.mark.parametrize("level,expected_color", [
        (NotifyLevel.INFO, 0x3498DB),
        (NotifyLevel.WARN, 0xF1C40F),
        (NotifyLevel.ERROR, 0xE74C3C),
        (NotifyLevel.CRITICAL, 0xE74C3C),
    ])
    def test_level_color_mapping(self, level, expected_color):
        session = FakeSession()
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/abc", session=session)
        notifier.send(_event(level=level))
        assert session.last_json["embeds"][0]["color"] == expected_color

    def test_no_webhook_configured_returns_false_and_does_not_post(self, monkeypatch):
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        session = FakeSession()
        notifier = DiscordNotifier(webhook_url=None, session=session)

        result = notifier.send(_event())

        assert result is False
        assert session.calls == 0

    def test_session_post_raising_is_swallowed(self):
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/abc",
                                   session=RaisingSession())
        result = notifier.send(_event())
        assert result is False

    def test_non_2xx_response_returns_false(self):
        session = FakeSession(status_code=500)
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/abc", session=session)
        result = notifier.send(_event())
        assert result is False


# ── LogNotifier / NullNotifier ───────────────────────────────────────────────────

class TestLogAndNullNotifier:

    def test_log_notifier_returns_true(self, caplog):
        result = LogNotifier().send(_event())
        assert result is True

    def test_null_notifier_returns_false(self):
        result = NullNotifier().send(_event())
        assert result is False


# ── MultiNotifier ─────────────────────────────────────────────────────────────────

class _AlwaysTrue(NotifierBackend):
    def send(self, event):
        return True


class _AlwaysFalse(NotifierBackend):
    def send(self, event):
        return False


class TestMultiNotifier:

    def test_true_if_any_backend_succeeds(self):
        multi = MultiNotifier([_AlwaysFalse(), _AlwaysTrue()])
        assert multi.send(_event()) is True

    def test_false_if_all_backends_fail(self):
        multi = MultiNotifier([_AlwaysFalse(), _AlwaysFalse()])
        assert multi.send(_event()) is False


# ── get_notifier() factory ───────────────────────────────────────────────────────

class TestGetNotifier:

    def test_default_is_log_notifier(self, monkeypatch):
        monkeypatch.delenv("VOSS_NOTIFY", raising=False)
        assert isinstance(get_notifier(), LogNotifier)

    def test_log_env_returns_log_notifier(self, monkeypatch):
        monkeypatch.setenv("VOSS_NOTIFY", "log")
        assert isinstance(get_notifier(), LogNotifier)

    def test_discord_env_returns_discord_notifier(self, monkeypatch):
        monkeypatch.setenv("VOSS_NOTIFY", "discord")
        assert isinstance(get_notifier(), DiscordNotifier)

    def test_null_env_returns_null_notifier(self, monkeypatch):
        monkeypatch.setenv("VOSS_NOTIFY", "null")
        assert isinstance(get_notifier(), NullNotifier)

    def test_multi_env_returns_multi_notifier(self, monkeypatch):
        monkeypatch.setenv("VOSS_NOTIFY", "multi")
        assert isinstance(get_notifier(), MultiNotifier)

    def test_unknown_env_falls_back_to_log_notifier(self, monkeypatch):
        monkeypatch.setenv("VOSS_NOTIFY", "carrier-pigeon")
        assert isinstance(get_notifier(), LogNotifier)

    def test_env_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("VOSS_NOTIFY", "DISCORD")
        assert isinstance(get_notifier(), DiscordNotifier)
