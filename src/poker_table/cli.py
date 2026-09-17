"""Command-line entry point (`poker-table`)."""

from __future__ import annotations

import argparse

from poker_table import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="poker-table", description=__doc__)
    parser.add_argument("--version", action="version", version=f"poker-table {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
