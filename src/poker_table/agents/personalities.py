"""LLM seat personalities: a system prompt plus the knobs that shape cost and style."""

from __future__ import annotations

from dataclasses import dataclass

# What a seat is handed beyond the bare table, each one switchable so its worth can be
# measured (see ``poker-table eval``):
#   odds      the probability calculator: equity against random hands, outs, pot odds
#   ranges    each opponent's range read from this hand's action, and equity against it
#   reads     a HUD from earlier hands: style, VPIP/PFR, aggression, hands they showed
#   notes     a notebook the model writes to itself and reads back in later hands
#   playbook  a short primer on sizing and hand selection, in the system prompt
AIDS: tuple[str, ...] = ("odds", "ranges", "reads", "notes", "playbook")


def check_aids(aids) -> frozenset[str]:
    unknown = set(aids) - set(AIDS)
    if unknown:
        raise ValueError(f"unknown aid {', '.join(sorted(unknown))}; choose from {', '.join(AIDS)}")
    return frozenset(aids)


@dataclass(frozen=True, slots=True)
class Personality:
    key: str
    prompt: str
    effort: str = "medium"  # low | medium | high | xhigh | max
    # Preflop, facing a raise, hands scoring below this on the Chen scale fold without a
    # model call. None disables the gate (the maniac wants to see every flop).
    auto_fold_below: float | None = 5
    talkative: bool = True
    aids: tuple[str, ...] = AIDS
    description: str = ""  # one line for listings; the prompt is for the model


PERSONALITIES: dict[str, Personality] = {
    "maniac": Personality(
        "maniac",
        "You are THE MANIAC. You love pressure: you raise light, three-bet with air, barrel "
        "every street and needle the table with trash talk. You would rather lose a big pot "
        "than fold a small one. You are not stupid though — when someone finally plays back "
        "at you with real strength, you notice.",
        effort="low",
        auto_fold_below=None,
    ),
    "rock": Personality(
        "rock",
        "You are THE ROCK. You wait patiently for premium hands and play them straight-"
        "forwardly for value. You fold marginal hands without regret, rarely bluff, and say "
        "little at the table. When you do bet big, you have it.",
        effort="low",
        auto_fold_below=8,
        talkative=False,
    ),
    "nerd": Personality(
        "nerd",
        "You are THE MATH NERD. Every decision is pot odds, equity, ranges and fold equity. "
        "You estimate your equity against the opponents' likely ranges, compare it with the "
        "price you are getting, and size bets by the pot. You bluff exactly as often as the "
        "math says you should. Your table talk quotes percentages.",
        effort="high",
        auto_fold_below=4,
    ),
    "storyteller": Personality(
        "storyteller",
        "You are THE STORYTELLER. You play a balanced, thoughtful game and narrate your hand "
        "in character: every bet tells a story and you make sure the story is consistent from "
        "preflop to river. You enjoy a well-told bluff and a well-timed hero call, and you "
        "chat warmly with the table without ever revealing your actual cards.",
        effort="medium",
        auto_fold_below=5,
    ),
}


def get_personality(key: str) -> Personality:
    try:
        return PERSONALITIES[key]
    except KeyError:
        raise ValueError(
            f"unknown personality {key!r}; choose from {', '.join(sorted(PERSONALITIES))}"
        ) from None
