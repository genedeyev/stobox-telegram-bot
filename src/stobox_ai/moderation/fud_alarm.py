"""Real-time FUD spike detector.

A single skeptic isn't an emergency — but several FUD messages in a short window
(coordinated or spreading) is something admins want to know about immediately,
not in tomorrow's digest. This tracks FUD events per chat and fires at most one
alert per cooldown once a spike crosses the threshold.

Pure, in-memory, time-injectable so it's easy to test. State resets on restart,
which is fine: spike detection is inherently about the recent moment.
"""

from __future__ import annotations

from datetime import datetime, timedelta


class FudAlarm:
    def __init__(self, threshold: int = 3, window_min: int = 10, cooldown_min: int = 30) -> None:
        self.threshold = max(1, threshold)
        self.window = timedelta(minutes=window_min)
        self.cooldown = timedelta(minutes=cooldown_min)
        self._events: dict[str, list[datetime]] = {}
        self._last_alert: dict[str, datetime] = {}

    def record(self, chat_id: str, now: datetime) -> tuple[bool, int]:
        """Record one FUD message. Returns (should_alert, count_in_window).

        Fires once the count within the window reaches the threshold, then stays
        quiet for the cooldown so admins aren't spammed while a wave rolls on.
        """
        recent = [t for t in self._events.get(chat_id, []) if now - t <= self.window]
        recent.append(now)
        self._events[chat_id] = recent
        count = len(recent)
        if count < self.threshold:
            return False, count
        last = self._last_alert.get(chat_id)
        if last is not None and now - last < self.cooldown:
            return False, count
        self._last_alert[chat_id] = now
        return True, count
