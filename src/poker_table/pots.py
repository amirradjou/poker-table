"""Main/side pot construction and payout splitting.

Pots are derived purely from what each seat put in over the whole hand, which handles
every all-in shape (several short stacks, folds after partial contributions) with one
rule: a seat is eligible for the part of the pot it matched.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Pot:
    amount: int
    eligible: tuple[int, ...]  # seat indexes that can win it, in seat order


def uncalled_amount(contributions: Mapping[int, int]) -> tuple[int, int] | None:
    """The (seat, amount) of a bet nobody matched, or None if the top contribution is shared."""
    if len(contributions) < 2:
        return None
    ordered = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
    (top_seat, top), (_, second) = ordered[0], ordered[1]
    if top > second:
        return top_seat, top - second
    return None


def build_pots(contributions: Mapping[int, int], in_hand: Iterable[int]) -> list[Pot]:
    """Split the chips into a main pot and side pots.

    ``contributions`` maps seat index -> chips put in (after any uncalled bet was
    returned); ``in_hand`` lists seats that have not folded. Consecutive layers with the
    same eligible seats are merged so a fold does not create a pointless extra pot.
    """
    live = set(in_hand)
    levels = sorted({c for c in contributions.values() if c > 0})
    pots: list[Pot] = []
    previous = 0
    for level in levels:
        amount = sum(max(0, min(c, level) - previous) for c in contributions.values())
        eligible = tuple(sorted(s for s in live if contributions.get(s, 0) >= level))
        previous = level
        if amount == 0:
            continue
        if pots and pots[-1].eligible == eligible:
            pots[-1] = Pot(pots[-1].amount + amount, eligible)
        else:
            pots.append(Pot(amount, eligible))
    return pots


def split_amount(
    amount: int, winners: Iterable[int], first_after: int, num_seats: int
) -> dict[int, int]:
    """Divide ``amount`` among ``winners``; odd chips go clockwise from the button."""
    winners = sorted(winners)
    if not winners:
        raise ValueError("no winners to pay")
    share, remainder = divmod(amount, len(winners))
    payout = dict.fromkeys(winners, share)
    order = sorted(winners, key=lambda s: (s - first_after) % num_seats)
    for seat in order[:remainder]:
        payout[seat] += 1
    return payout
