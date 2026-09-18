"""Importers that turn site hand-history exports into :class:`HandHistory` records."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from poker_table.coach.importers.pokerstars import ImportError_, ImportResult, parse_pokerstars
from poker_table.history import HandHistory


def detect_format(text: str) -> str | None:
    head = text.lstrip()[:200]
    if (
        head.startswith("PokerStars ")
        or "PokerStars Hand #" in head
        or "PokerStars Zoom Hand #" in head
    ):
        return "pokerstars"
    return None


def import_text(text: str) -> ImportResult:
    kind = detect_format(text)
    if kind is None:
        raise ImportError_("unrecognised hand history format (PokerStars text is supported)")
    return parse_pokerstars(text)


def import_files(paths: list[Path]) -> Iterator[tuple[Path, ImportResult]]:
    for path in paths:
        yield path, import_text(path.read_text(encoding="utf-8", errors="replace"))


__all__ = [
    "HandHistory",
    "ImportError_",
    "ImportResult",
    "detect_format",
    "import_files",
    "import_text",
]
