"""A human at the terminal."""

from __future__ import annotations

from collections.abc import Callable

from poker_table.agents.base import Decision, SeatView
from poker_table.engine import Action, ActionType

HELP = (
    "commands: f=fold  k=check  c=call  b <amount>=bet  r <amount>=raise to  a=all-in  "
    "say <text>=table talk (sent with your next action)  ?=help"
)


class HumanAgent:
    """Prints the seat's view and reads one action per turn from ``input_fn``."""

    kind = "human"

    def __init__(
        self,
        name: str = "you",
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.name = name
        self._input = input_fn
        self._output = output_fn

    def act(self, view: SeatView) -> Decision:
        self._output("\n" + view.describe(numbers=True))
        talk = ""
        while True:
            try:
                raw = self._input(f"{self.name}> ").strip()
            except EOFError:
                return Decision(_safe(view), reasoning="input closed", table_talk=talk)
            if not raw or raw == "?":
                self._output(HELP)
                continue
            if raw.lower().startswith("say "):
                talk = raw[4:].strip()
                self._output(f'(you will say: "{talk}")')
                continue
            action, problem = parse_command(raw, view)
            if action is None:
                self._output(f"{problem} — legal: {view.legal.describe()}")
                continue
            return Decision(action, reasoning="human", table_talk=talk)


def parse_command(raw: str, view: SeatView) -> tuple[Action | None, str]:
    """Turn ``"r 12"`` into an Action for this view, or (None, why-not)."""
    legal = view.legal
    parts = raw.lower().split()
    verb, args = parts[0], parts[1:]
    match verb:
        case "f" | "fold":
            return Action.fold(), ""
        case "k" | "x" | "check":
            return (Action.check(), "") if legal.can_check else (None, "you cannot check")
        case "c" | "call":
            if legal.can_call:
                return Action.call(), ""
            return (Action.check(), "") if legal.can_check else (None, "nothing to call")
        case "a" | "allin" | "all-in" | "shove":
            if legal.can_raise:
                amount = legal.max_raise_to
                return _sized(amount, legal), ""
            return (Action.call(), "") if legal.can_call else (None, "you cannot raise")
        case "b" | "bet" | "r" | "raise":
            if not legal.can_raise:
                return None, "you cannot bet or raise here"
            if not args or not args[0].lstrip("-").isdigit():
                return None, f"give an amount ({legal.min_raise_to}..{legal.max_raise_to})"
            amount = int(args[0])
            if not legal.min_raise_to <= amount <= legal.max_raise_to:
                return None, f"amount must be {legal.min_raise_to}..{legal.max_raise_to}"
            return _sized(amount, legal), ""
    return None, f"unknown command {verb!r} (? for help)"


def _sized(amount: int, legal) -> Action:
    return Action(ActionType.BET if legal.is_bet else ActionType.RAISE, amount)


def _safe(view: SeatView) -> Action:
    return Action.check() if view.legal.can_check else Action.fold()
