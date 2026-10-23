"""Ensure core and service layers remain independent of Discord."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_core_and_services_do_not_import_discord() -> None:
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for package in ("core", "services")
        for path in (PROJECT_ROOT / "tianling" / package).rglob("*.py")
        if "discord" in _imported_roots(path)
    ]
    assert offenders == []
