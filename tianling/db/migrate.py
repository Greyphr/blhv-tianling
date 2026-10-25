"""Run Alembic migrations from application code (used by the bot at startup)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic.config import Config

from alembic import command

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["configure_logger"] = False  # keep the bot's own logging setup
    return cfg


async def ensure_schema() -> None:
    """Upgrade the database to the latest revision (idempotent).

    Alembic's async env calls ``asyncio.run``, so it must run in a worker thread
    when the bot's event loop is already running.
    """
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    log.info("database schema is at head")
