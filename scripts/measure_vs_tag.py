"""How a seat does against each bot, next to tag sitting in the same seat on the same deals.

    uv run python scripts/measure_vs_tag.py exploiter                 # heads-up, 2,000 hands each
    uv run python scripts/measure_vs_tag.py exploiter --five-handed   # also the benchmark's table
    uv run python scripts/measure_vs_tag.py exploiter --hands 400 --control rock
    uv run python scripts/measure_vs_tag.py exploiter --vs fish,lag   # other opponents

Each match is played twice from seed 2026: once with the seat being measured in seat 0, once
with the control (tag unless ``--control`` says otherwise) in that seat instead. Everything else
is the same: the decks, the first button and the seed of every other seat. So the two runs
differ only in who answers in seat 0, and their difference is paired hand for hand: the seat's
all-in adjusted chips in hand n minus the control's in hand n, as bb/100 with a 95% interval.
A pairing removes the luck of the deal that both runs share, so its interval is narrower than
the gap between two separate intervals would suggest.

Heads-up is against tag, rock, maniac and station, at the heads-up track's stakes (blinds 1/2,
200 chips topped up every hand). ``--five-handed`` adds the benchmark's table: the seat with tag,
rock, maniac and station. ``--vs fish,lag,...`` names other opponents instead: one heads-up
match against each, and with ``--five-handed`` one table of the seat with all of them (so with
five names it is six-handed). Each row also counts how the seat decided, by the ``method`` its
decisions record (a seat that records none shows ``-``). When decisions record a ``rule`` (as
exploiter's do), a line under the table counts them by rule.

Measuring the control against itself gives a difference of exactly zero, which a test checks.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

from poker_table.agents.registry import make_agents
from poker_table.history import HandHistory
from poker_table.league import LeagueConfig, run_league
from poker_table.margins import all_in_adjusted, margin_of_error, seat_margins

SEED = 2026
HANDS = 2000
OPPONENTS = ("tag", "rock", "maniac", "station")
SEAT = "entrant"  # the seat both runs put in seat 0


@dataclass(frozen=True, slots=True)
class Arm:
    adjusted_bb_per_100: float
    margin: float | None
    methods: Counter[str]
    rules: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True, slots=True)
class Pairing:
    hands: int
    seat: Arm
    control: Arm
    difference: float  # bb/100, the seat minus the control
    margin: float | None


def load() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def per_hand(histories: list[HandHistory]) -> dict[str, float]:
    """Seat 0's all-in adjusted result in each hand, in big blinds, by hand id."""
    results = {}
    for history in histories:
        worth = all_in_adjusted(history)
        player = next(p for p in history.players if p.name == SEAT)
        chips = player.net if worth is None else worth[player.seat]
        results[history.hand_id] = chips / history.big_blind
    return results


def play(seat: str, others: tuple[str, ...], hands: int, seed: int) -> tuple[Arm, dict[str, float]]:
    """One run: ``seat`` in seat 0 against ``others``, from ``seed``."""
    agents = make_agents([f"{SEAT}:{seat}", *others], seed=seed)
    result = run_league(agents, LeagueConfig(hands=hands, buy_in=200, seed=seed))
    methods: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    for history in result.histories:
        for d in history.decisions:
            if history.players[d.seat].name != SEAT:
                continue
            if d.illegal or d.reasoning.startswith("agent error"):
                raise SystemExit(f"{seat} broke in hand {history.hand_id}: {d.reasoning}")
            methods[str(d.meta.get("method", "-"))] += 1
            if d.meta.get("rule"):
                rules[str(d.meta["rule"])] += 1
    margin = seat_margins(result.histories)[SEAT]
    arm = Arm(margin.adjusted_bb_per_100, margin.adjusted_margin, methods, rules)
    return arm, per_hand(result.histories)


def paired(
    seat: str, others: tuple[str, ...], hands: int = HANDS, seed: int = SEED, control: str = "tag"
) -> Pairing:
    """The seat and the control, each in seat 0 against ``others`` on the same deals."""
    mine, my_hands = play(seat, others, hands, seed)
    theirs, their_hands = play(control, others, hands, seed)
    if my_hands.keys() != their_hands.keys():
        raise SystemExit("the two runs dealt different hands, so they cannot be paired")
    gaps = [my_hands[h] - their_hands[h] for h in my_hands]
    return Pairing(hands, mine, theirs, statistics.fmean(gaps) * 100, margin_of_error(gaps))


def cell(value: float, margin: float | None) -> str:
    return f"{value:+.0f} ±{margin:.0f}" if margin is not None else f"{value:+.0f}"


def methods(arm: Arm) -> str:
    total = sum(arm.methods.values())
    if not total or set(arm.methods) == {"-"}:
        return "-"
    return ", ".join(f"{name} {count / total:.0%}" for name, count in arm.methods.most_common())


def tables(
    opponents: tuple[str, ...] = OPPONENTS, five_handed: bool = False
) -> list[tuple[str, tuple[str, ...]]]:
    """The matches to play: heads-up against each opponent, then all of them at one table."""
    rows: list[tuple[str, tuple[str, ...]]] = [(f"vs {o}", (o,)) for o in opponents]
    if five_handed:
        label = "five-handed" if len(opponents) == 4 else f"{len(opponents) + 1}-handed"
        rows.append((label, opponents))
    return rows


def opponents_of(text: str | None) -> tuple[str, ...]:
    """``--vs fish,lag`` as a tuple of seats; the default opponents when it is not given."""
    if text is None:
        return OPPONENTS
    names = tuple(name.strip() for name in text.split(",") if name.strip())
    if not names:
        raise SystemExit("--vs needs at least one seat")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("seat", help="the seat to measure, as --seats takes it: exploiter, ...")
    parser.add_argument("--control", default="tag", help="who sits in seat 0 instead (tag)")
    parser.add_argument("--hands", type=int, default=HANDS, help=f"hands a match ({HANDS:,})")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--five-handed", action="store_true", help="also the benchmark's table")
    parser.add_argument(
        "--vs", metavar="A,B,...", help=f"opponents instead of {','.join(OPPONENTS)}"
    )
    args = parser.parse_args()

    started = time.monotonic()
    print(f"start: {load()}", file=sys.stderr)
    matches = tables(opponents_of(args.vs), args.five_handed)
    print(
        f"{args.seat} in seat 0, then {args.control} in its place on the same deals: "
        f"{args.hands:,} hands a match from seed {args.seed}, all-in adjusted bb/100 with a 95% "
        "interval; the difference is paired hand for hand."
    )
    print()
    print(f"| table | {args.seat} | {args.control} in its seat | difference | how it decided |")
    print("|---|---:|---:|---:|---|")
    fired: list[tuple[str, Counter[str]]] = []
    for label, others in matches:
        p = paired(args.seat, others, args.hands, args.seed, args.control)
        fired.append((label, p.seat.rules))
        print(
            f"| {label} | {cell(p.seat.adjusted_bb_per_100, p.seat.margin)} "
            f"| {cell(p.control.adjusted_bb_per_100, p.control.margin)} "
            f"| {cell(p.difference, p.margin)} | {methods(p.seat)} |",
            flush=True,
        )
    if any(rules for _, rules in fired):
        print()
        print("Decisions changed, by the rule their meta records:")
        for label, rules in fired:
            counts = ", ".join(f"{n} {c:,}" for n, c in sorted(rules.items())) or "none"
            print(f"- {label}: {counts}")
    print(f"took {(time.monotonic() - started) / 60:.1f} min; end: {load()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
