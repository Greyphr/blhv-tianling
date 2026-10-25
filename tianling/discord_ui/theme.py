"""Celestial theme constants."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CelestialTheme:
    color_primary: int = 0x7C9BFF
    color_success: int = 0x7CFFB2
    color_warning: int = 0xFFE37C
    color_danger: int = 0xFF7C7C
    color_info: int = 0xB9A6FF
    emoji_star: str = "✨"
    emoji_moon: str = "🌙"
    emoji_sun: str = "☀️"
    emoji_registry: str = "📜"
    emoji_team: str = "🤝"
    emoji_profile: str = "🪪"
    emoji_success: str = "✅"
    footer: str = "THE HEAVENLY REGISTRY • Tianling"


THEME = CelestialTheme()
