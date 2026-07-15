"""FUD spike detector tests (offline, injected clock)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from stobox_ai.moderation.fud_alarm import FudAlarm


def _t(base, mins):
    return base + timedelta(minutes=mins)


def test_no_alert_below_threshold():
    a = FudAlarm(threshold=3, window_min=10, cooldown_min=30)
    base = datetime(2026, 7, 15, tzinfo=UTC)
    assert a.record("g", base) == (False, 1)
    assert a.record("g", _t(base, 1)) == (False, 2)


def test_alert_fires_at_threshold():
    a = FudAlarm(threshold=3, window_min=10, cooldown_min=30)
    base = datetime(2026, 7, 15, tzinfo=UTC)
    a.record("g", base)
    a.record("g", _t(base, 1))
    fired, count = a.record("g", _t(base, 2))
    assert fired is True and count == 3


def test_cooldown_suppresses_repeat_alerts():
    a = FudAlarm(threshold=3, window_min=60, cooldown_min=30)
    base = datetime(2026, 7, 15, tzinfo=UTC)
    for i in range(3):
        a.record("g", _t(base, i))          # 3rd fires
    # More FUD within cooldown → no new alert.
    fired, _ = a.record("g", _t(base, 5))
    assert fired is False
    # After cooldown elapses, it can fire again.
    fired2, _ = a.record("g", _t(base, 40))
    assert fired2 is True


def test_window_evicts_old_events():
    a = FudAlarm(threshold=3, window_min=10, cooldown_min=30)
    base = datetime(2026, 7, 15, tzinfo=UTC)
    a.record("g", base)
    a.record("g", _t(base, 1))
    # 20 min later the first two are outside the window → count resets, no alert.
    fired, count = a.record("g", _t(base, 20))
    assert fired is False and count == 1


def test_per_chat_isolation():
    a = FudAlarm(threshold=2, window_min=10, cooldown_min=30)
    base = datetime(2026, 7, 15, tzinfo=UTC)
    a.record("g1", base)
    fired, _ = a.record("g2", base)          # different chat, still count 1
    assert fired is False
