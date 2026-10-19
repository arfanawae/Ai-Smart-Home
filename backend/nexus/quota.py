"""Daily LLM request counter. Gemini free-tier daily quotas reset at midnight Pacific time."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RESET_TZ = ZoneInfo("America/Los_Angeles")
WARN_RATIO = 0.8

log = logging.getLogger(__name__)


class QuotaTracker:
    def __init__(self, path: Path, daily_limit: int | None) -> None:
        self._path = path
        self.daily_limit = daily_limit
        self._day = ""
        self.used = 0
        self.exhausted = False  # set after a 429: stop calling until the next reset
        self._save_failed = False  # warn once, not on every request
        self._paused_until = 0.0  # per-minute rate limit: in memory only, it lasts seconds
        self._load()

    @staticmethod
    def _today() -> str:
        return datetime.now(RESET_TZ).date().isoformat()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self._day = data.get("day", "")
        self.used = int(data.get("used", 0))
        self.exhausted = bool(data.get("exhausted", False))
        self._roll_over()

    def _save(self) -> None:
        """Best effort: the counter is bookkeeping, so a file it cannot write never stops a turn."""
        payload = {"day": self._day, "used": self.used, "exhausted": self.exhausted}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as e:  # PermissionError, disk full, file locked by another program
            if not self._save_failed:
                log.warning("cannot save the LLM usage counter to %s (%s); counting in memory only", self._path, e)
            self._save_failed = True
        else:
            self._save_failed = False

    def _roll_over(self) -> None:
        today = self._today()
        if self._day != today:
            self._day, self.used, self.exhausted = today, 0, False

    def pause(self, seconds: float) -> None:
        """Gemini said "too many requests per minute": hold off for that long, not for the whole day."""
        self._paused_until = max(self._paused_until, time.monotonic() + max(0.0, seconds))

    @property
    def paused_for(self) -> float:
        return max(0.0, self._paused_until - time.monotonic())

    def can_request(self) -> bool:
        self._roll_over()
        if self.exhausted or self.paused_for > 0:
            return False
        return self.daily_limit is None or self.used < self.daily_limit

    def record(self) -> None:
        self._roll_over()
        self.used += 1
        self._save()

    def mark_exhausted(self) -> None:
        self._roll_over()
        self.exhausted = True
        self._save()

    @property
    def near_limit(self) -> bool:
        return self.daily_limit is not None and self.used >= self.daily_limit * WARN_RATIO

    @staticmethod
    def next_reset_local() -> datetime:
        now = datetime.now(RESET_TZ)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.astimezone()  # system local time (Thailand on the Acer laptop)

    def summary_th(self) -> str:
        limit = "ไม่จำกัดฝั่ง NEXUS" if self.daily_limit is None else f"{self.daily_limit}"
        state = " · หมดแล้ว (Gemini แจ้ง 429)" if self.exhausted else ""
        return f"ใช้ไป {self.used} / {limit} request วันนี้{state} · รีเซ็ต {self.next_reset_local():%H:%M} น."
