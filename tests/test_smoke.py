import poker_table
from poker_table.cli import main


def test_version_is_set() -> None:
    assert poker_table.__version__


def test_cli_parses_no_args() -> None:
    assert main([]) == 0
