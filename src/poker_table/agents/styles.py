"""More scripted styles: fish, lag, calculator, trapper and tilter.

None of these change the bots in ``scripted.py``. ``fish`` and ``lag`` are their own rules with
their own random stream. ``calculator`` draws nothing at random. ``trapper`` and ``tilter`` hold a
tag inside and ask it at every decision, as ``exploiter`` does, so until they depart from it they
play exactly the hands a tag seat with the same seed would.

* ``fish``: loose-passive. Limps and calls with most hands, rarely raises. After the flop it goes
  on with a pair or a draw, folds air to a bet, and bets only with top pair or better.
* ``lag``: loose-aggressive. Opens and 3-bets far wider than tag, c-bets most flops, sometimes
  fires a second barrel, and gives up to a raise without top pair or better.
* ``calculator``: its equity against a random hand for each opponent still in (``odds.py``).
  It calls when that equity beats the price, bets or raises above a threshold, never bluffs.
* ``trapper``: a tag that slow-plays two pair or better made on the flop. It checks or calls the
  flop, raises a bet after it checked (a check-raise), and bets or raises the turn and river.
* ``tilter``: a tag until it loses ``TILT_BB`` big blinds or more in a hand that reached a
  showdown. Then it plays like a maniac for the next ``TILT_HANDS`` hands, and calms down. A
  loss while on tilt does not make the tilt longer.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import TYPE_CHECKING

from poker_table.agents.base import Decision, SeatView
from poker_table.agents.scripted import Maniac, TightAggressive, raise_or_bet
from poker_table.agents.strength import (
    MadeHand,
    chen_score,
    classify,
    has_flush_draw,
    has_open_ender,
    outs_equity,
)
from poker_table.cards import Card, Rank, Suit
from poker_table.engine import Action, ActionType, Street
from poker_table.odds import chances

if TYPE_CHECKING:  # imported for types only: history imports the agents package
    from poker_table.history import HandHistory

_EARLY = {"UTG", "UTG+1", "UTG+2", "MP", "LJ"}
_LATE = {"BTN", "BTN/SB", "SB"}
_AGGRESSIVE = (ActionType.BET, ActionType.RAISE)


def _made(view: SeatView) -> MadeHand:
    return classify(view.hole, view.board)


def _outs(view: SeatView) -> int:
    flush = has_flush_draw(view.hole, view.board)
    straight = has_open_ender(view.hole, view.board)
    return (9 if flush else 0) + (8 if straight else 0)


def _passive(view: SeatView) -> Action:
    return Action.check() if view.legal.can_check else Action.call()


def _check_or_fold(view: SeatView) -> Action:
    return Action.check() if view.legal.can_check else Action.fold()


def _limpers(view: SeatView) -> int:
    return sum(
        1 for e in view.street_actions if e.action is not None and e.action.type is ActionType.CALL
    )


def _i_was_aggressor(view: SeatView, street: Street) -> bool:
    """Whether this seat made the last bet or raise on ``street``."""
    last = None
    for e in view.events:
        if e.street is street and e.action is not None and e.action.type in _AGGRESSIVE:
            last = e.seat
    return last == view.seat


def _i_did(view: SeatView, kind: tuple[ActionType, ...]) -> bool:
    """Whether this seat already did one of ``kind`` on this street."""
    return any(
        e.seat == view.seat and e.action is not None and e.action.type in kind
        for e in view.street_actions
    )


def _name(made: MadeHand, view: SeatView) -> str:
    return f"{made.name.lower().replace('_', ' ')} on {view.street.value}"


# ----- fish -------------------------------------------------------------------------------


class Fish:
    """A loose-passive recreational player who does fold: calls a lot, bets only good hands."""

    kind = "fish"

    PLAYS = 2  # limps or calls a raise with a Chen score of at least this (about 80% of hands)
    CALL_RAISE_BB = 8  # ... a raise that costs at most this many big blinds (or 15% of its stack)
    RAISES = 12  # raises first with a Chen score this high, at most half the time

    def __init__(self, name: str = "fish", seed: int = 0) -> None:
        self.name = name
        self._rng = random.Random(seed)

    def act(self, view: SeatView) -> Decision:
        if view.street is Street.PREFLOP:
            return self._preflop(view)
        return self._postflop(view)

    def _preflop(self, view: SeatView) -> Decision:
        score = chen_score(view.hole)
        legal = view.legal
        bb = view.big_blind
        why = f"Chen {score:g} from {view.position}"
        if view.current_bet <= bb:
            if score >= self.RAISES and legal.can_raise and self._rng.random() < 0.5:
                size = 3 * bb + _limpers(view) * bb
                return Decision(raise_or_bet(view, size), f"{why}: a good one, raise")
            if score >= self.PLAYS:
                return Decision(_passive(view), f"{why}: let's see a flop")
            return Decision(_check_or_fold(view), f"{why}: not this one")
        if score >= 16 and legal.can_raise and self._rng.random() < 0.3:
            return Decision(raise_or_bet(view, view.current_bet * 3), f"{why}: re-raise")
        cheap = view.to_call <= max(self.CALL_RAISE_BB * bb, 0.15 * view.stack)
        if score >= self.PLAYS and cheap:
            return Decision(Action.call(), f"{why}: call the raise")
        return Decision(_check_or_fold(view), f"{why}: too much")

    def _postflop(self, view: SeatView) -> Decision:
        made = _made(view)
        legal = view.legal
        why = _name(made, view)
        if made >= MadeHand.TOP_PAIR:
            if not view.to_call:
                rate = 0.8 if made is MadeHand.STRONG else 0.6
                if legal.can_raise and self._rng.random() < rate:
                    return Decision(raise_or_bet(view, round(view.pot * 0.5)), f"{why}: bet")
                return Decision(Action.check(), f"{why}: check")
            if made is MadeHand.STRONG and legal.can_raise and self._rng.random() < 0.15:
                return Decision(raise_or_bet(view, view.current_bet * 3), f"{why}: raise")
            return Decision(Action.call(), f"{why}: call")
        outs = _outs(view)
        if made is MadeHand.WEAK_PAIR or outs:
            if outs and made is MadeHand.NOTHING:
                why = f"a draw ({outs} outs) on {view.street.value}"
            if not view.to_call:
                return Decision(Action.check(), f"{why}: check")
            if view.to_call <= view.pot:
                return Decision(Action.call(), f"{why}: I'll see one more")
            return Decision(Action.fold(), f"{why}: too big")
        return Decision(_check_or_fold(view), f"{why}: missed")


# ----- lag --------------------------------------------------------------------------------


class LooseAggressive:
    """Opens and 3-bets wide, c-bets most flops and sometimes barrels, but folds to a raise."""

    kind = "lag"

    LOOSER = 3  # Chen points under tag's opening thresholds
    THREE_BET = 9  # 3-bets with at least this (tag needs 12)
    LIGHT = 6  # and, in late position, sometimes with at least this
    CALL = 6  # calls a raise with at least this
    CBET = 0.8  # c-bets this share of flops it raised before
    BARREL = 0.4  # bets the turn after its flop bet with nothing, this often (0.7 with a draw)

    def __init__(self, name: str = "lag", seed: int = 0) -> None:
        self.name = name
        self._rng = random.Random(seed)

    def act(self, view: SeatView) -> Decision:
        if view.street is Street.PREFLOP:
            return self._preflop(view)
        return self._postflop(view)

    def _open_threshold(self, view: SeatView) -> float:
        base = 9 if view.position in _EARLY else 7 if view.position in _LATE else 8
        if len(view.players) <= 6:
            base -= 1
        return base - self.LOOSER

    def _preflop(self, view: SeatView) -> Decision:
        score = chen_score(view.hole)
        legal = view.legal
        bb = view.big_blind
        why = f"Chen {score:g} from {view.position}"
        if view.stack + view.street_bet <= 15 * bb and legal.can_raise:
            if score >= 7:
                return Decision(raise_or_bet(view, legal.max_raise_to), f"{why}: short, shove")
            return Decision(_check_or_fold(view), f"{why}: short, wait")

        if view.current_bet <= bb:
            if score >= self._open_threshold(view) and legal.can_raise:
                size = 3 * bb + _limpers(view) * bb
                return Decision(raise_or_bet(view, size), f"{why}: open to {size}")
            return Decision(_check_or_fold(view), f"{why}: fold")

        if _i_did(view, (ActionType.RAISE, ActionType.BET)):  # it raised, and was raised again
            if score >= 14 and legal.can_raise:
                return Decision(raise_or_bet(view, view.current_bet * 3), f"{why}: four-bet")
            if score >= 10 and view.to_call <= 0.3 * view.stack:
                return Decision(Action.call(), f"{why}: call the re-raise")
            return Decision(_check_or_fold(view), f"{why}: fold to the re-raise")
        light = view.position in _LATE and score >= self.LIGHT and self._rng.random() < 0.35
        if legal.can_raise and (score >= self.THREE_BET or light):
            label = "re-raise" if score >= self.THREE_BET else "light re-raise"
            return Decision(raise_or_bet(view, view.current_bet * 3), f"{why}: {label}")
        if score >= self.CALL and view.to_call <= max(0.2 * view.stack, 4 * bb):
            return Decision(Action.call(), f"{why}: call a raise")
        return Decision(_check_or_fold(view), f"{why}: fold to a raise")

    def _postflop(self, view: SeatView) -> Decision:
        made = _made(view)
        legal = view.legal
        pot = view.pot
        outs = _outs(view)
        equity = outs_equity(outs, 2 if view.street is Street.FLOP else 1)
        why = _name(made, view)
        roll = self._rng.random()  # one draw a decision, whichever branch is taken

        if view.to_call and _i_did(view, _AGGRESSIVE):  # it bet, and was raised
            if made is MadeHand.STRONG and legal.can_raise:
                return Decision(raise_or_bet(view, view.current_bet * 3), f"{why}: re-raise")
            if made is MadeHand.TOP_PAIR and view.to_call <= 0.5 * pot:
                return Decision(Action.call(), f"{why}: call the raise")
            return Decision(Action.fold(), f"{why}: raised, give up")

        if not view.to_call:
            if not legal.can_raise:
                return Decision(Action.check(), f"{why}: check")
            if made is MadeHand.STRONG:
                return Decision(raise_or_bet(view, round(pot * 0.75)), f"{why}: value")
            if made is MadeHand.TOP_PAIR:
                return Decision(raise_or_bet(view, round(pot * 0.6)), f"{why}: value")
            if view.street is Street.FLOP and _i_was_aggressor(view, Street.PREFLOP):
                if view.active_players <= 4 and roll < self.CBET:
                    return Decision(raise_or_bet(view, round(pot * 0.5)), f"{why}: c-bet")
            if view.street is Street.TURN and _i_was_aggressor(view, Street.FLOP):
                if roll < (0.7 if outs else self.BARREL):
                    return Decision(raise_or_bet(view, round(pot * 0.6)), f"{why}: second barrel")
            if outs and roll < 0.5:
                return Decision(raise_or_bet(view, round(pot * 0.5)), f"{why}: semi-bluff")
            return Decision(Action.check(), f"{why}: check")

        if made is MadeHand.STRONG:
            if legal.can_raise and roll < 0.7:
                return Decision(raise_or_bet(view, view.current_bet * 3), f"{why}: raise")
            return Decision(Action.call(), f"{why}: call")
        if made is MadeHand.TOP_PAIR:
            if view.to_call <= 0.75 * pot:
                return Decision(Action.call(), f"{why}: call")
            return Decision(Action.fold(), f"{why}: too expensive")
        if made is MadeHand.WEAK_PAIR and view.to_call <= 0.4 * pot:
            return Decision(Action.call(), f"{why}: cheap call")
        if outs:
            if legal.can_raise and roll < 0.2:
                return Decision(
                    raise_or_bet(view, view.current_bet * 3), f"{why}: semi-bluff raise"
                )
            if view.pot_odds <= equity:
                return Decision(Action.call(), f"{why}: priced in")
        return Decision(Action.fold(), f"{why}: fold")


# ----- calculator -------------------------------------------------------------------------

PREFLOP_SAMPLES = 1000  # equity before the flop, computed once per hand class and table size
SAMPLES = 300  # after the flop, per decision; a single unknown hand on the river is counted
VALUE = 1.3  # bets with at least this many times its fair share of the pot (1 / players in)
RAISE = 1.6  # raises with at least this many times its fair share
MOST = 0.85  # neither threshold goes above this equity

_preflop_equity: dict[tuple[int, int, bool, int], float] = {}


def preflop_equity(hole: Sequence[Card], opponents: int) -> float:
    """Equity against ``opponents`` random hands, by hand class (AKs, 72o, ...), cached."""
    a, b = sorted((c.rank for c in hole), reverse=True)
    suited = hole[0].suit == hole[1].suit
    key = (int(a), int(b), suited, opponents)
    if key not in _preflop_equity:
        cards = (Card(Rank(a), Suit.SPADES), Card(Rank(b), Suit.SPADES if suited else Suit.HEARTS))
        result = chances(cards, (), opponents, samples=PREFLOP_SAMPLES, seed=key[0] * 100 + key[1])
        _preflop_equity[key] = result.equity
    return _preflop_equity[key]


def equity_of(view: SeatView) -> tuple[float, int]:
    """This seat's equity against a random hand for each opponent still in, and their count."""
    opponents = max(1, sum(1 for p in view.players if p.seat != view.seat and not p.folded))
    if view.street is Street.PREFLOP:
        return preflop_equity(view.hole, opponents), opponents
    return chances(view.hole, view.board, opponents, samples=SAMPLES, seed=0).equity, opponents


class Calculator:
    """Plays by its computed chance of winning against random hands, and nothing else.

    A pure function of the view: its sampling is seeded the same way every time, so it draws
    nothing from a random stream and answers a spot the same way whenever it is asked.
    """

    kind = "calculator"

    def __init__(self, name: str = "calculator", seed: int = 0) -> None:
        self.name = name
        self.seed = seed  # unused: kept so every factory takes one

    def act(self, view: SeatView) -> Decision:
        equity, opponents = equity_of(view)
        fair = 1 / (opponents + 1)
        bet_at = min(MOST, VALUE * fair)
        raise_at = min(MOST, RAISE * fair)
        price = view.pot_odds
        legal = view.legal
        meta = {
            "method": "equity",
            "equity": round(equity, 4),
            "price": round(price, 4),
            "opponents": opponents,
        }
        why = f"{equity:.0%} against {opponents} random hand{'s' if opponents > 1 else ''}"
        preflop = view.street is Street.PREFLOP
        bb = view.big_blind

        if not view.to_call:
            if legal.can_raise and equity >= bet_at:
                size = 3 * bb + _limpers(view) * bb if preflop else round(view.pot * 0.66)
                return Decision(raise_or_bet(view, size), f"{why}: value", meta=meta)
            return Decision(Action.check(), f"{why}: check", meta=meta)
        if legal.can_raise and equity >= raise_at and equity > price:
            size = 3 * bb + _limpers(view) * bb if view.current_bet <= bb else view.current_bet * 3
            return Decision(raise_or_bet(view, size), f"{why}: raise for value", meta=meta)
        if equity > price:
            return Decision(Action.call(), f"{why}, the price is {price:.0%}: call", meta=meta)
        return Decision(Action.fold(), f"{why}, the price is {price:.0%}: fold", meta=meta)


# ----- trapper ----------------------------------------------------------------------------


class Trapper:
    """A tag that slow-plays two pair or better made on the flop, then springs the trap."""

    kind = "trapper"

    def __init__(self, name: str = "trapper", *, seed: int = 0) -> None:
        self.name = name
        self.tag = TightAggressive(name, seed=seed, kind="trapper")

    def act(self, view: SeatView) -> Decision:
        tag = self.tag.act(view)  # always asked, so its random choices run as tag's
        trap = self._trap(view)
        if trap is None:
            return Decision(tag.action, tag.reasoning, meta={"method": "tag"})
        action, why = trap
        # the trap line says "trap", even where tag would have played the same action
        return Decision(action, f"{_name(MadeHand.STRONG, view)}: {why}", meta={"method": "trap"})

    def _trap(self, view: SeatView) -> tuple[Action, str] | None:
        if view.street is Street.PREFLOP or classify(view.hole, view.board[:3]) < MadeHand.STRONG:
            return None
        if _made(view) < MadeHand.STRONG:
            return None  # the board took it away; tag decides
        legal = view.legal
        if view.street is Street.FLOP:
            if not view.to_call:
                return Action.check(), "slow-play, check"
            if _i_did(view, (ActionType.CHECK,)) and legal.can_raise:
                return raise_or_bet(view, view.current_bet * 3), "check-raise"
            return Action.call(), "slow-play, just call"
        if not legal.can_raise:
            return None
        if view.to_call:
            return raise_or_bet(view, view.current_bet * 3), "the trap: raise"
        return raise_or_bet(view, round(view.pot * 0.75)), "the trap: bet"


# ----- tilter -----------------------------------------------------------------------------

TILT_BB = 40  # losing at least this many big blinds in a hand that reached showdown tilts it
TILT_HANDS = 15  # and it plays like a maniac for this many hands after that one


class Tilter:
    """A tag that goes on tilt after a big loss at showdown, plays like a maniac, then calms.

    The tilt is counted from the hands the league shows it (``observe``), so a run rebuilt from
    the same hands tilts at the same hands.
    """

    kind = "tilter"

    def __init__(self, name: str = "tilter", *, seed: int = 0) -> None:
        self.name = name
        self.tag = TightAggressive(name, seed=seed, kind="tilter")
        self.maniac = Maniac(name, seed=seed + 1_000_003)  # drawn from only on tilt
        self.tilt_left = 0  # hands still to play on tilt
        self._just_tilted = False  # the first decision on tilt says so
        self._just_calmed = False  # and so does the first one after it

    def observe(self, history: HandHistory) -> None:
        """A hand just played, as this seat saw it (``history.public_view``)."""
        me = next((p for p in history.players if p.name == self.name), None)
        if me is None:
            return
        if self.tilt_left:  # a hand played on tilt counts down, and never adds to the tilt
            self.tilt_left -= 1
            self._just_calmed = not self.tilt_left
            return
        if me.seat in history.showdown and me.net <= -TILT_BB * history.big_blind:
            self.tilt_left = TILT_HANDS
            self._just_tilted = True
            self._just_calmed = False

    @property
    def on_tilt(self) -> bool:
        return self.tilt_left > 0

    def act(self, view: SeatView) -> Decision:
        tag = self.tag.act(view)  # always asked, so its random choices run as tag's
        meta: dict[str, object] = {"tilt": self.on_tilt, "tilt_hands_left": self.tilt_left}
        if not self.on_tilt:
            talk = ""
            if self._just_calmed:
                talk, self._just_calmed = "Okay. Deep breath. Back to basics.", False
            return Decision(
                tag.action, tag.reasoning, table_talk=talk, meta={"method": "tag", **meta}
            )
        wild = self.maniac.act(view)
        if self._just_tilted:
            talk, self._just_tilted = "Unbelievable. That's it, I'm not folding anything.", False
        else:
            talk = "still steaming" if wild.action.type in _AGGRESSIVE else ""
        return Decision(
            wild.action,
            f"on tilt ({self.tilt_left} hands to go): {wild.reasoning}",
            table_talk=talk,
            meta={"method": "tilt", **meta},
        )
