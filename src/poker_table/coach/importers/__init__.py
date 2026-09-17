"""Importers that turn site hand-history exports into :class:`HandHistory` records."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from poker_table.coach.importers.builder import ImportError_
from poker_table.coach.importers.eight88 import parse_888
from poker_table.coach.importers.pokerstars import ImportResult, parse_pokerstars
from poker_table.history import HandHistory

SUPPORTED = "PokerStars, GGPoker and 888poker text"


def detect_format(text: str) -> str | None:
    head = text.lstrip("﻿ \t\r\n")[:200]
    if (
        head.startswith("PokerStars ")
        or "PokerStars Hand #" in head
        or "PokerStars Zoom Hand #" in head
    ):
        return "pokerstars"
    if head.startswith("Poker Hand #"):
        return "ggpoker"
    if "888poker Hand History" in head:
        return "888"
    return None


def import_text(text: str) -> ImportResult:
    kind = detect_format(text)
    if kind is None:
        raise ImportError_(f"unrecognised hand history format ({SUPPORTED} are supported)")
    if kind == "888":
        return parse_888(text)
    return parse_pokerstars(text)  # GGPoker writes the same dialect


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
