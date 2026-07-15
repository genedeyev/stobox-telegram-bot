"""Deleted-account detection tests (offline)."""

from __future__ import annotations

from dataclasses import dataclass

from stobox_ai.moderation.deleted import is_deleted_account


@dataclass
class FakeUser:
    first_name: str | None = ""
    last_name: str | None = None
    username: str | None = None
    is_bot: bool = False


def test_ghost_account_is_detected():
    assert is_deleted_account(FakeUser(first_name="", last_name=None, username=None)) is True
    assert is_deleted_account(FakeUser(first_name="   ")) is True   # whitespace only


def test_real_user_not_flagged():
    assert is_deleted_account(FakeUser(first_name="Gene")) is False
    assert is_deleted_account(FakeUser(first_name="", username="gene")) is False   # has handle
    assert is_deleted_account(FakeUser(first_name="", last_name="Deyev")) is False  # has last name


def test_bot_never_flagged():
    assert is_deleted_account(FakeUser(first_name="", is_bot=True)) is False


def test_none_is_safe():
    assert is_deleted_account(None) is False
