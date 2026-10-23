"""Tests for the Tianling scaffold."""

from __future__ import annotations

from tianling.config import Settings


def test_settings_fields() -> None:
    expected = {"discord_token", "guild_id", "database_url", "log_level"}
    assert expected <= set(Settings.model_fields)
