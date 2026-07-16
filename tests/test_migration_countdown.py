"""Public migration-countdown cadence (offline)."""

from __future__ import annotations

from datetime import date, timedelta

from stobox_ai.channels.telegram.proactive import ProactiveScheduler

due = ProactiveScheduler._countdown_due


def _monday():
    d = date(2026, 9, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


def test_final_week_is_daily():
    any_day = date(2026, 9, 10)
    for d in range(0, 8):
        assert due(d, any_day) is True


def test_within_month_every_three_days():
    any_day = date(2026, 8, 16)
    assert due(30, any_day) and due(15, any_day) and due(9, any_day)
    assert not due(16, any_day) and not due(29, any_day)


def test_far_out_is_weekly_on_mondays():
    mon = _monday()
    assert mon.weekday() == 0
    assert due(45, mon) is True                       # Monday → post
    assert due(45, mon + timedelta(days=1)) is False  # Tuesday → skip
