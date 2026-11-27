"""The coarse heads-up game the cfr:blueprint seat was trained on, in plain Python.

Training (``scripts/train_blueprint.py``) and the seat both build the game from here, so the
seat plays exactly the tree it was trained on and computes exactly the buckets it was trained
with.

**Betting.** Heads-up no-limit with both players starting the same stack. On each street a
player who has not faced a bet or raise yet may check, bet half the pot, bet the pot or go all
in; one facing a bet may fold, call, raise the pot or go all in. Sizes are fractions of the pot
after calling, rounded to a chip, never below the table's minimum raise. At most ``cap`` bets
and raises a street, after which only fold or call is left. Preflop the big blind is not
counted, so the small blind's first move uses the opening sizes (to 2 or 3 big blinds).

**Cards.** Preflop, the 169 classes of starting hands, nothing lost. After the flop, a bucket
from the hand's strength now (``wins2 / (2 * totals)``: its chance of beating one random hand on
this board, ties as a half) and, on the flop and turn, whether it holds a flush draw or an
open-ended straight draw. Each street's bucket forgets the earlier ones (imperfect recall).

**Format 2** of the strategy file adds a little memory: after the flop the seat's state is
(preflop group, bucket), where the group is one of a few groups of the 169 classes, listed in
the file. State (g, b) is numbered ``g * buckets + b`` (``grouped_state``), as the solver's
``solvers/recall.py`` numbers it. A format-1 file has no groups.

**Translation.** Actions that are not in the tree (an opponent's bet of 0.7 pot) are mapped to a
neighbouring size with the pseudo-harmonic mapping of Ganzfried and Sandholm (IJCAI 2013,
https://www.ijcai.org/Proceedings/13/Papers/028.pdf).
"""

from __future__ import annotations

import json
import struct
import zlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from functools import cache

from poker_table.cards import Card
from poker_table.evaluator import evaluate

PREFLOP, FLOP, TURN, RIVER = range(4)
DECISION, CHANCE, FOLD, SHOWDOWN = "decision", "chance", "fold", "showdown"
ALL_IN = "a"
MAGIC = b"PTBLUEPRINT"  # then the format's number and a newline
FORMATS = (1, 2)  # 1: buckets that forget; 2: buckets that also keep a preflop group


@dataclass(frozen=True, slots=True)
class Betting:
    stack: int = 200
    small_blind: int = 1
    big_blind: int = 2
    open_sizes: tuple[float, ...] = (0.5, 1.0)
    raise_sizes: tuple[float, ...] = (1.0,)
    cap: int = 3


@dataclass(frozen=True, slots=True)
class Cards:
    """Hand-strength cut points per street. Flop and turn: ``levels`` cut points times two (a
    draw or not); river: ``river`` cut points."""

    flop: tuple[float, ...] = ()
    turn: tuple[float, ...] = ()
    river: tuple[float, ...] = ()

    def buckets(self, street: int) -> int:
        if street == PREFLOP:
            return 169
        if street == RIVER:
            return len(self.river) + 1
        return 2 * (len(self.flop if street == FLOP else self.turn) + 1)


@dataclass(slots=True)
class Spot:
    """One node of the abstract game, with the chips that got it there."""

    kind: str
    street: int
    actor: int = -1  # 0 is the small blind (the button), 1 the big blind
    put_in: tuple[int, int] = (0, 0)  # each player's chips in the pot
    street_bets: tuple[int, int] = (0, 0)
    labels: tuple[str, ...] = ()
    amounts: tuple[int, ...] = ()  # the raise-to amount of each label (0 for fold, check, call)
    children: list[Spot] = field(default_factory=list)
    folder: int = -1
    index: int = -1
    last_raise: int = 0
    raises: int = 0
    acted: tuple[bool, bool] = (False, False)

    @property
    def pot(self) -> int:
        return self.put_in[0] + self.put_in[1]

    def child(self, label: str) -> Spot:
        return self.children[self.labels.index(label)]


def raise_target(fraction: float, current_bet: int, pot: int, to_call: int) -> int:
    """A bet or raise of ``fraction`` of the pot after calling, as the street total to raise to."""
    return current_bet + int(fraction * (pot + to_call) + 0.5)


def build_tree(betting: Betting) -> Spot:
    """The whole betting tree, decision nodes numbered depth-first as the solver numbers them."""
    sb, bb = betting.small_blind, betting.big_blind
    root = _decision(
        betting,
        street=PREFLOP,
        actor=0,
        put_in=(sb, bb),
        bets=(sb, bb),
        last_raise=bb,
        raises=0,
        acted=(False, False),
    )
    number(root)
    return root


def number(root: Spot) -> list[Spot]:
    found: list[Spot] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.kind == DECISION:
            node.index = len(found)
            found.append(node)
        stack.extend(reversed(node.children))
    return found


def _decision(betting, *, street, actor, put_in, bets, last_raise, raises, acted) -> Spot:
    me, other = actor, 1 - actor
    stack = betting.stack
    current = max(bets)
    to_call = current - bets[me]
    left = stack - put_in[me]
    node = Spot(
        DECISION,
        street,
        actor,
        put_in,
        bets,
        last_raise=last_raise,
        raises=raises,
        acted=acted,
    )
    labels: list[str] = []
    amounts: list[int] = []
    children: list[Spot] = []
    if to_call > 0:
        labels.append("f")
        amounts.append(0)
        children.append(Spot(FOLD, street, put_in=put_in, folder=me))
    # check or call
    pay = min(to_call, left)
    paid = _with(put_in, me, put_in[me] + pay)
    called_bets = _with(bets, me, bets[me] + pay)
    labels.append("c")
    amounts.append(0)
    children.append(
        _after_call(
            betting, street, actor, paid, called_bets, last_raise, raises, _with(acted, me, True)
        )
    )
    opponent_all_in = put_in[other] == stack
    if raises < betting.cap and left > to_call and not opponent_all_in:
        sizes = betting.open_sizes if raises == 0 else betting.raise_sizes
        pot = put_in[0] + put_in[1]
        all_in_to = bets[me] + left
        minimum = current + max(last_raise, betting.big_blind) if current else betting.big_blind
        targets: set[int] = set()
        for fraction in [*sizes, None]:
            if fraction is None:
                target, label = all_in_to, ALL_IN
            else:
                target = max(raise_target(fraction, current, pot, to_call), minimum)
                label = f"b{fraction:g}"
                if target >= all_in_to:
                    target, label = all_in_to, ALL_IN
            if label in labels or target in targets:
                continue  # two sizes that come to the same chips are one action
            targets.add(target)
            size = target - current
            full = size >= last_raise
            new_bets = _with(bets, me, target)
            new_put = _with(put_in, me, put_in[me] + target - bets[me])
            new_acted = (True, True) if not full else _with((False, False), me, True)
            labels.append(label)
            amounts.append(target)
            children.append(
                _decision(
                    betting,
                    street=street,
                    actor=other,
                    put_in=new_put,
                    bets=new_bets,
                    last_raise=size if full else last_raise,
                    raises=raises + 1,
                    acted=new_acted,
                )
            )
    node.labels, node.amounts, node.children = tuple(labels), tuple(amounts), children
    return node


def _after_call(betting, street, actor, put_in, bets, last_raise, raises, acted) -> Spot:
    """What a check or call leads to: the other player's turn, or the end of the street."""
    other = 1 - actor
    if not (acted[0] and acted[1] and bets[0] == bets[1]):
        return _decision(
            betting,
            street=street,
            actor=other,
            put_in=put_in,
            bets=bets,
            last_raise=last_raise,
            raises=raises,
            acted=acted,
        )
    if betting.stack in put_in or street == RIVER:
        return Spot(SHOWDOWN, street, put_in=put_in)
    nxt = street + 1
    first = _decision(
        betting,
        street=nxt,
        actor=1,  # the big blind acts first after the flop
        put_in=put_in,
        bets=(0, 0),
        last_raise=betting.big_blind,
        raises=0,
        acted=(False, False),
    )
    return Spot(CHANCE, nxt, put_in=put_in, children=[first])


def _with(pair: tuple, index: int, value) -> tuple:
    return (value, pair[1]) if index == 0 else (pair[0], value)


# ----- buckets -------------------------------------------------------------------------------


@cache
def straight_outs(mask: int) -> int:
    """How many ranks would complete a straight for these ranks (bit r is rank r + 2) that are
    not one already; 0 when they already hold a straight."""
    if _has_straight(mask):
        return 0
    return sum(1 for r in range(13) if not mask >> r & 1 and _has_straight(mask | 1 << r))


def _has_straight(mask: int) -> bool:
    ranks = mask << 1 | (mask >> 12 & 1)  # shift up one, and put the ace below the deuce too
    return any(ranks >> low & 0b11111 == 0b11111 for low in range(10))


def rank_mask(cards: Sequence[Card]) -> int:
    mask = 0
    for card in cards:
        mask |= 1 << (int(card.rank) - 2)
    return mask


def has_draw(hole: Sequence[Card], board: Sequence[Card]) -> bool:
    """A flush draw or an open-ended straight draw (or two gutshots) that uses a hole card."""
    cards = [*hole, *board]
    for suit in {c.suit for c in hole}:
        if sum(1 for c in cards if c.suit == suit) == 4:
            if not any(sum(1 for c in cards if c.suit == s) >= 5 for s in {c.suit for c in cards}):
                return True
    mine = straight_outs(rank_mask(cards))
    return mine >= 2 and mine > straight_outs(rank_mask(board))


def strength_counts(hole: Sequence[Card], board: Sequence[Card]) -> tuple[int, int]:
    """Twice the hands this one beats plus the ties, and how many hands it was compared with:
    every two cards the other player could hold on this board."""
    from poker_table.cards import FULL_DECK

    seen = {*hole, *board}
    rest = [c for c in FULL_DECK if c not in seen]
    mine = evaluate([*hole, *board])
    wins2 = total = 0
    for i, first in enumerate(rest):
        for second in rest[i + 1 :]:
            theirs = evaluate([first, second, *board])
            wins2 += 2 if mine > theirs else 1 if mine == theirs else 0
            total += 1
    return wins2, total


def level(strength: float, cuts: Sequence[float]) -> int:
    return sum(1 for cut in cuts if strength >= cut)


def grouped_state(group: int, bucket: int, buckets: int) -> int:
    """The number of state (preflop group, bucket) on a street with ``buckets`` buckets."""
    return group * buckets + bucket


def bucket(cards: Cards, hole: Sequence[Card], board: Sequence[Card]) -> int:
    """The seat's bucket on this street: a class of starting hands, or a bucket of hands."""
    from poker_table.agents.handclass import class_index

    if not board:
        return class_index(*hole)
    wins2, total = strength_counts(hole, board)
    strength = wins2 / (2 * total)
    street = {3: FLOP, 4: TURN, 5: RIVER}[len(board)]
    if street == RIVER:
        return level(strength, cards.river)
    cuts = cards.flop if street == FLOP else cards.turn
    return level(strength, cuts) + (len(cuts) + 1) * has_draw(hole, board)


# ----- translation ---------------------------------------------------------------------------


def pseudo_harmonic(smaller: float, larger: float, size: float) -> float:
    """The chance of reading a bet of ``size`` as ``smaller`` rather than ``larger``, all three as
    fractions of the pot: f(x) = (B - x)(1 + A) / ((B - A)(1 + x)), Ganzfried and Sandholm."""
    if not smaller <= size <= larger or smaller == larger:
        raise ValueError(f"{size} is not between {smaller} and {larger}")
    return (larger - size) * (1 + smaller) / ((larger - smaller) * (1 + size))


# ----- the strategy file ---------------------------------------------------------------------


def quantize(probabilities: Sequence[float]) -> bytes:
    """Probabilities as bytes that add up to exactly 255, largest remainders first."""
    scaled = [max(p, 0.0) * 255 for p in probabilities]
    whole = [int(s) for s in scaled]
    short = 255 - sum(whole)
    order = sorted(range(len(scaled)), key=lambda i: (-(scaled[i] - whole[i]), i))
    for i in order[:short]:
        whole[i] += 1
    return bytes(whole)


def write_strategy(
    path,
    betting: Betting,
    cards: Cards,
    rows: Sequence[bytes],
    header: dict,
    groups: Sequence[int] = (),
) -> None:
    """Format 1 without ``groups``; format 2, which keeps a preflop group, with them."""
    payload = dict(header, betting=asdict(betting), cards=asdict(cards))
    if groups:
        payload["groups"] = [int(g) for g in groups]
    head = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    blob = zlib.compress(b"".join(rows), 9)
    version = 2 if groups else 1
    with open(path, "wb") as fh:
        fh.write(MAGIC + b"%d\n" % version + struct.pack(">I", len(head)) + head + blob)


@dataclass(frozen=True, slots=True)
class Strategy:
    betting: Betting
    cards: Cards
    header: dict
    root: Spot
    nodes: tuple[Spot, ...]
    table: bytes
    offsets: tuple[int, ...]  # where each decision node's rows start in ``table``
    groups: tuple[int, ...] = ()  # format 2: the preflop group of each of the 169 classes

    @property
    def version(self) -> int:
        return 2 if self.groups else 1

    def rows(self, street: int) -> int:
        """How many states a player can be in on ``street``: one row of the table each."""
        if street == PREFLOP or not self.groups:
            return self.cards.buckets(street)
        return (max(self.groups) + 1) * self.cards.buckets(street)

    def state(self, hole: Sequence[Card], board: Sequence[Card]) -> int:
        """The row this hand plays on this board."""
        held = bucket(self.cards, hole, board)
        if not board or not self.groups:
            return held
        from poker_table.agents.handclass import class_index

        street = {3: FLOP, 4: TURN, 5: RIVER}[len(board)]
        group = self.groups[class_index(*hole)]
        return grouped_state(group, held, self.cards.buckets(street))

    def probabilities(self, node: Spot, state: int) -> list[float]:
        width = len(node.labels)
        start = self.offsets[node.index] + state * width
        return [b / 255 for b in self.table[start : start + width]]


def read_strategy(data: bytes) -> Strategy:
    if not data.startswith(MAGIC) or b"\n" not in data[: len(MAGIC) + 8]:
        raise ValueError("not a blueprint strategy file")
    end = data.index(b"\n")
    version = data[len(MAGIC) : end].decode("ascii", "replace")
    if version not in {str(f) for f in FORMATS}:
        known = " and ".join(str(f) for f in FORMATS)
        raise ValueError(
            f"a blueprint strategy file in format {version}; this version of poker-table reads "
            f"formats {known}"
        )
    at = end + 1
    (length,) = struct.unpack(">I", data[at : at + 4])
    header = json.loads(data[at + 4 : at + 4 + length])
    table = zlib.decompress(data[at + 4 + length :])
    b = header["betting"]
    betting = Betting(
        stack=b["stack"],
        small_blind=b["small_blind"],
        big_blind=b["big_blind"],
        open_sizes=tuple(b["open_sizes"]),
        raise_sizes=tuple(b["raise_sizes"]),
        cap=b["cap"],
    )
    c = header["cards"]
    cards = Cards(flop=tuple(c["flop"]), turn=tuple(c["turn"]), river=tuple(c["river"]))
    groups: tuple[int, ...] = ()
    if version == "2":
        groups = tuple(int(g) for g in header["groups"])
        if len(groups) != 169 or set(groups) != set(range(max(groups) + 1)):
            raise ValueError("a format-2 file needs a group 0, 1, ... for each of the 169 classes")
    root = build_tree(betting)
    nodes = tuple(number(root))
    strategy = Strategy(betting, cards, header, root, nodes, table, (), groups)
    offsets, at = [], 0
    for node in nodes:
        offsets.append(at)
        at += strategy.rows(node.street) * len(node.labels)
    if at != len(table):
        raise ValueError(f"the strategy has {len(table)} bytes and the tree needs {at}")
    return Strategy(betting, cards, header, root, nodes, table, tuple(offsets), groups)
