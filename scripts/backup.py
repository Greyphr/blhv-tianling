"""SQLite backup script."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def backup_sqlite(db_path: str | Path, backup_dir: str | Path) -> Path:
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"tianling_{ts}.db"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dest


if __name__ == "__main__":
    backup_sqlite("data/tianling.db", "backups")
