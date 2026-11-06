"""Check what docs/experiments.md says about a published league against its hands.

    uv run python scripts/check_league.py docs/results/local-league/hands.jsonl

Prints each figure the write-up quotes. Scoring the showdowns does not use the engine's evaluator,
so a bug there would show up as a pot paid to the wrong seat. Takes about two minutes.
"""

from __future__ import annotations

import argparse
import collections
import itertools
import random
from collections.abc import Sequence
from pathlib import Path

from poker_table.cards import parse_cards
from poker_table.engine import Action, Hand, Player
from poker_table.history import HandHistory, read_jsonl
from poker_table.odds import table_equities

RANKS = "23456789TJQKA"
ALL_IN_HANDS = 40_000
SAMPLES = 20_000


def score_five(cards: Sequence[str]) -> tuple[int, ...]:
    """Rank five cards, written from scratch so that it shares nothing with the engine."""
    ranks = sorted((RANKS.index(card[0]) for card in cards), reverse=True)
    counts = sorted(collections.Counter(ranks).items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape = tuple(count for _, count in counts)
    order = tuple(rank for rank, _ in counts)
    flush = len({card[1] for card in cards}) == 1
    straight = None
    if shape == (1, 1, 1, 1, 1):
        if ranks[0] - ranks[4] == 4:
            straight = ranks[0]
        elif ranks == [12, 3, 2, 1, 0]:  # the wheel: the ace plays low
            straight = 3
    if straight is not None and flush:
        return (8, straight)
    if shape == (4, 1):
        return (7, *order)
    if shape == (3, 2):
        return (6, *order)
    if flush:
        return (5, *ranks)
    if straight is not None:
        return (4, straight)
    return ({(3, 1, 1): 3, (2, 2, 1): 2, (2, 1, 1, 1): 1}.get(shape, 0), *order)


def best_hand(hole: Sequence[str], board: Sequence[str]) -> tuple[int, ...]:
    return max(score_five(five) for five in itertools.combinations([*hole, *board], 5))


def check_payouts(hands: Sequence[HandHistory]) -> None:
    pots = wrong = 0
    for hand in hands:
        seats = {p.seat: p for p in hand.players}
        for pot in hand.pots:
            live = [seat for seat in pot["eligible"] if seat in hand.showdown]
            if len(live) < 2:
                continue
            scores = {seat: best_hand(seats[seat].hole, hand.board) for seat in live}
            top = max(scores.values())
            pots += 1
            wrong += not {seat for seat, s in scores.items() if s == top} <= set(hand.payouts)
    print(f"contested pots re-scored: {pots}, best hand not paid: {wrong}")


def check_deck(seed: int) -> None:
    """Every seat goes all in on every hand of the same seed stream, so each should get a fifth."""
    seeds = random.Random(seed)
    share: collections.Counter[int] = collections.Counter()
    for n in range(ALL_IN_HANDS):
        players = [Player(f"p{i}", 200) for i in range(5)]
        hand = Hand(players, button=n % 5, small_blind=1, big_blind=2, seed=seeds.randrange(2**63))
        while not hand.finished:
            legal = hand.legal_actions()
            hand.apply(Action.raise_to(legal.max_raise_to) if legal.can_raise else Action.call())
        total = sum(hand.payouts.values())
        for seat, amount in hand.payouts.items():
            share[seat] += amount / total
    shares = [100 * share[seat] / ALL_IN_HANDS for seat in range(5)]
    print(
        f"{ALL_IN_HANDS} five-way all-ins, share of the pot by seat: "
        f"{min(shares):.1f}% to {max(shares):.1f}%"
    )


def holes_of(hand: HandHistory, seats: Sequence[int]) -> dict:
    players = {p.seat: p for p in hand.players}
    return {seat: parse_cards("".join(players[seat].hole)) for seat in seats}


def check_luck(hands: Sequence[HandHistory]) -> None:
    """Showdowns won against what the cards were worth, in hands all in before the flop."""
    won: collections.Counter[str] = collections.Counter()
    worth: collections.Counter[str] = collections.Counter()
    count = 0
    for hand in hands:
        if len(hand.showdown) < 2 or {d.street for d in hand.decisions} != {"preflop"}:
            continue
        count += 1
        names = {p.seat: p.name for p in hand.players}
        shares, _ = table_equities(
            holes_of(hand, list(hand.showdown)), samples=SAMPLES, seed=hand.seed
        )
        for seat, share in shares.items():
            won[names[seat]] += seat in hand.payouts
            worth[names[seat]] += share
    print(f"{count} hands all in before the flop, showdowns won / what the cards were worth:")
    for name, value in worth.most_common():
        print(f"  {name}: {won[name]} / {value:.1f}")


def check_model_seats(hands: Sequence[HandHistory]) -> None:
    aggressive = all_in = oversized = 0
    illegal: collections.Counter[str] = collections.Counter()
    decisions: collections.Counter[str] = collections.Counter()
    before_flop: collections.Counter[str] = collections.Counter()
    gated: collections.Counter[str] = collections.Counter()
    largest = 0
    early = 0
    for hand in hands:
        players = {p.seat: p for p in hand.players}
        early += (
            len(hand.showdown) >= 2
            and hand.board[4:] != []
            and (not hand.decisions or hand.decisions[-1].street != "river")
        )
        actions = [event for event in hand.events if event.kind == "action"]
        for decision, event in zip(hand.decisions, actions, strict=True):
            player = players[decision.seat]
            if not player.kind.startswith("llm"):
                continue
            decisions[player.name] += 1
            before_flop[player.name] += decision.street == "preflop"
            illegal[player.name] += decision.illegal
            gated[player.name] += "cost_usd" not in decision.meta
            if decision.applied.split()[0] in ("bet", "raise"):
                aggressive += 1
                all_in += event.all_in
            verb, *amount = decision.requested.split()
            if verb in ("bet", "raise") and int(amount[0]) > player.stack:
                oversized += 1
                largest = max(largest, int(amount[0]))
    print(f"hands all in before the last card: {early} of {len(hands)}")
    print(f"model bets and raises: {aggressive}, all in: {all_in}")
    print(f"asked for more than a stack: {oversized}, the largest {largest}")
    for name, count in decisions.items():
        print(
            f"  {name}: {count} decisions, {before_flop[name]} before the flop, "
            f"{illegal[name]} illegal, {gated[name]} automatic folds"
        )


def check_illegal_folds(hands: Sequence[HandHistory]) -> None:
    """What calling would have been worth, instead of the fold an illegal raise turns into.

    A rough estimate: everyone else's choices are held fixed, though a call could change them.
    """
    faced_all_in = total = 0
    change = 0.0
    for hand in hands:
        players = {p.seat: p for p in hand.players}
        actions = [event for event in hand.events if event.kind == "action"]
        for index, decision in enumerate(hand.decisions):
            if not decision.illegal:
                continue
            total += 1
            faced_all_in += any(
                event.all_in and event.street == decision.street for event in actions[:index]
            )
            seat = players[decision.seat]
            put_in = hand.payouts.get(seat.seat, 0) - seat.net
            pot = sum(p["amount"] for p in hand.pots) + seat.stack - put_in
            shares, _ = table_equities(
                holes_of(hand, [*hand.showdown, seat.seat]), samples=SAMPLES, seed=hand.seed
            )
            change += shares[seat.seat] * pot - seat.stack - seat.net
    print(f"illegal moves: {total}, facing an all-in on the street: {faced_all_in}")
    print(f"calling them all instead would have changed the seat's result by {change:+.0f} chips")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hands", type=Path, help="a league's hands.jsonl")
    parser.add_argument("--seed", type=int, default=2026, help="the league's seed")
    args = parser.parse_args()
    hands = list(read_jsonl(args.hands))
    check_payouts(hands)
    check_deck(args.seed)
    check_luck(hands)
    check_model_seats(hands)
    check_illegal_folds(hands)


if __name__ == "__main__":
    main()
