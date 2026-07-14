"""
pipeline/notify.py
====================
The one observability/alerting seam. A solo operator runs this pipeline
unattended once a day — when a slate publishes clean, or a leakage check
fails, or quota is about to run out, something has to say so out loud.
Discord is the primary channel (a webhook posted from a phone-friendly
free tier); everything else (structured logs, tests, CI) just wants a
`NotifierBackend` that never throws.

    LogNotifier      — today's behavior: log the event at an appropriate
                        level. Default; keeps tests and the demo fully
                        offline and side-effect free.
    NullNotifier      — no-op; useful for explicitly silencing alerts.
    DiscordNotifier   — POST a Discord webhook embed. Best-effort: a
                        missing webhook, a network error, or a non-2xx
                        response is logged and swallowed — it must never
                        raise into the pipeline that triggered it.
    MultiNotifier     — fan out to several backends at once (e.g. always
                        log AND try Discord).

Backend is chosen by the `VOSS_NOTIFY` env var, read directly in this
module (mirrors the VOSS_STORAGE pattern in pipeline.config, but config.py
is not touched — this module owns its own env var).
Swapping log → Discord → both is a one-line change here, nowhere else.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Env var (see pipeline.config for the VOSS_STORAGE analog) ───────────────────
#   'log'     → LogNotifier (default; offline, tests + demo)
#   'discord' → DiscordNotifier (posts to DISCORD_WEBHOOK_URL)
#   'null'    → NullNotifier (explicitly silence alerts)
#   'multi'   → MultiNotifier([LogNotifier(), DiscordNotifier()])
#
# Read live (not cached at import time, unlike VOSS_STORAGE) inside
# get_notifier() below, so tests can monkeypatch it per-case.


# ── Levels ──────────────────────────────────────────────────────────────────────

class NotifyLevel:
    """String levels — plain constants keep events trivially JSON/log friendly."""

    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ── Event-name taxonomy ─────────────────────────────────────────────────────────
# Module-level constants so callers never hand-type event names that drift.

SLATE_PUBLISHED = "slate_published"
COVERAGE_GAP = "coverage_gap"
QUOTA_FLOOR = "quota_floor"
PUBLISH_OK = "publish_ok"
PUBLISH_FAILED = "publish_failed"
BACKTEST_DONE = "backtest_done"
LEAKAGE_FAIL = "leakage_fail"
MODEL_TRAINED = "model_trained"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class NotifyEvent:
    """One alert-worthy occurrence. Immutable — build one, send it, done."""

    name: str
    level: str
    title: str
    message: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    sport: str | None = None
    ts: str = field(default_factory=_utcnow_iso)


# ── Backends ────────────────────────────────────────────────────────────────────

class NotifierBackend(ABC):
    """Send one event somewhere. Never raise — return False on failure."""

    @abstractmethod
    def send(self, event: NotifyEvent) -> bool:
        """Deliver `event`. Returns True on (best-effort) success."""


_LOG_LEVEL_MAP = {
    NotifyLevel.INFO: logging.INFO,
    NotifyLevel.WARN: logging.WARNING,
    NotifyLevel.ERROR: logging.ERROR,
    NotifyLevel.CRITICAL: logging.CRITICAL,
}

# Discord embed side-bar colors (decimal, not hex string).
_DISCORD_COLOR_MAP = {
    NotifyLevel.INFO: 0x3498DB,      # blue
    NotifyLevel.WARN: 0xF1C40F,      # amber
    NotifyLevel.ERROR: 0xE74C3C,     # red
    NotifyLevel.CRITICAL: 0xE74C3C,  # red
}


class LogNotifier(NotifierBackend):
    """Offline default: log the event at the level it carries."""

    def send(self, event: NotifyEvent) -> bool:
        level = _LOG_LEVEL_MAP.get(event.level, logging.INFO)
        logger.log(
            level, "[notify] %s | %s: %s %s",
            event.name, event.title, event.message, event.fields or "",
        )
        return True


class NullNotifier(NotifierBackend):
    """Explicitly silence alerts. Always reports failure (nothing was sent)."""

    def send(self, event: NotifyEvent) -> bool:
        return False


class DiscordNotifier(NotifierBackend):
    """Post a Discord webhook embed. Best-effort — construction stays cheap;
    a missing webhook or a network hiccup is logged and swallowed, never
    raised into the pipeline that triggered the alert.
    """

    TIMEOUT = 10

    def __init__(self, webhook_url: str | None = None, session: Any = None):
        self._webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "")
        self._session = session or requests.Session()

    def send(self, event: NotifyEvent) -> bool:
        if not self._webhook_url:
            logger.warning(
                "DiscordNotifier: no webhook configured (DISCORD_WEBHOOK_URL); "
                "dropping event %s", event.name,
            )
            return False

        embed: dict[str, Any] = {
            "title": event.title,
            "description": event.message,
            "color": _DISCORD_COLOR_MAP.get(event.level, _DISCORD_COLOR_MAP[NotifyLevel.INFO]),
            "fields": [
                {"name": str(k), "value": str(v), "inline": True}
                for k, v in event.fields.items()
            ],
            "footer": {"text": f"{event.name} · {event.sport or 'all'}"},
        }
        payload = {"embeds": [embed]}

        try:
            resp = self._session.post(self._webhook_url, json=payload, timeout=self.TIMEOUT)
            status = getattr(resp, "status_code", 200)
            if not (200 <= status < 300):
                logger.warning(
                    "DiscordNotifier: webhook returned status %s for event %s",
                    status, event.name,
                )
                return False
            return True
        except Exception:
            logger.exception("DiscordNotifier: failed to post event %s", event.name)
            return False


class MultiNotifier(NotifierBackend):
    """Fan out to several backends. Succeeds if any one of them does."""

    def __init__(self, backends: list[NotifierBackend]):
        self._backends = backends

    def send(self, event: NotifyEvent) -> bool:
        return any([backend.send(event) for backend in self._backends])


def get_notifier() -> NotifierBackend:
    """Factory. Reads VOSS_NOTIFY ('log' default, 'discord', 'null', 'multi')."""
    backend = os.getenv("VOSS_NOTIFY", "log").lower()
    if backend == "log":
        return LogNotifier()
    if backend == "discord":
        return DiscordNotifier()
    if backend == "null":
        return NullNotifier()
    if backend == "multi":
        return MultiNotifier([LogNotifier(), DiscordNotifier()])
    logger.warning("Unknown VOSS_NOTIFY backend %r; falling back to LogNotifier", backend)
    return LogNotifier()


# ── Convenience helpers ──────────────────────────────────────────────────────────

def notify(event: NotifyEvent) -> bool:
    """Send a pre-built event via the configured notifier."""
    return get_notifier().send(event)


def notify_info(name: str, title: str, message: str = "", sport: str | None = None,
                 **fields: Any) -> bool:
    return notify(NotifyEvent(name=name, level=NotifyLevel.INFO, title=title,
                               message=message, sport=sport, fields=fields))


def notify_warn(name: str, title: str, message: str = "", sport: str | None = None,
                 **fields: Any) -> bool:
    return notify(NotifyEvent(name=name, level=NotifyLevel.WARN, title=title,
                               message=message, sport=sport, fields=fields))


def notify_error(name: str, title: str, message: str = "", sport: str | None = None,
                  **fields: Any) -> bool:
    return notify(NotifyEvent(name=name, level=NotifyLevel.ERROR, title=title,
                               message=message, sport=sport, fields=fields))
