"""Command-line entry point (`poker-table`)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from poker_table import __version__
from poker_table.agents.human import HumanAgent
from poker_table.agents.registry import available_kinds, make_agents
from poker_table.history import HandHistory, filter_by_date, read_jsonl, write_jsonl
from poker_table.league import (
    DEFAULT_SCHEDULE,
    LeagueConfig,
    Level,
    TournamentConfig,
    run_league,
    run_tournament,
)
from poker_table.stats import compute_stats, format_table

DEFAULT_SEATS = "tag,rock,maniac,station,random"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="poker-table",
        description="A no-limit hold'em table for scripted bots, LLM agents and humans.",
    )
    parser.add_argument("--version", action="version", version=f"poker-table {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play", help="play a session between agents and print the leaderboard")
    play.add_argument(
        "-n",
        "--hands",
        type=int,
        help="hands to play (default 100; with --tournament, the hand limit, default 5000)",
    )
    play.description = (
        "Play a session. Seat yourself with the kind 'human' (e.g. --seats you:human,tag,maniac):"
        " each of your turns prints the table and reads a command (? for help)."
    )
    play.add_argument(
        "-s",
        "--seats",
        default=DEFAULT_SEATS,
        help=(
            "comma-separated seats, each 'kind' or 'name:kind' "
            f"(kinds: {', '.join(available_kinds())})"
        ),
    )
    play.add_argument("--seed", type=int, default=0, help="league seed (default 0)")
    play.add_argument("--blinds", help="small/big blind, e.g. 1/2 (default)")
    play.add_argument("--ante", type=int, help="chips every seat posts before the blinds")
    play.add_argument("--stack", type=int, default=200, help="buy-in in chips (default 200)")
    play.add_argument(
        "--carry", action="store_true", help="carry stacks between hands and rebuy when busted"
    )
    play.add_argument(
        "--tournament",
        action="store_true",
        help="play a freezeout instead: rising blinds, no rebuys, until one seat has every chip",
    )
    play.add_argument(
        "--levels",
        help=(
            "tournament blind schedule, e.g. '1/2,2/4,5/10+1' (sb/bb+ante); "
            "the default doubles every level and antes from level 4"
        ),
    )
    play.add_argument(
        "--level-hands", type=int, default=20, help="hands per tournament level (default 20)"
    )
    play.add_argument("-o", "--out", type=Path, help="append every hand history to this JSONL file")
    play.add_argument(
        "--show", action="store_true", help="print each hand as it is played (with reasoning)"
    )
    play.add_argument("-q", "--quiet", action="store_true", help="only print the leaderboard")

    stats = sub.add_parser("stats", help="leaderboard and exploitability stats for a JSONL file")
    stats.add_argument("file", type=Path)

    replay = sub.add_parser("replay", help="print hands from a JSONL file")
    replay.add_argument("file", type=Path)
    replay.add_argument("--hand", help="hand id to show (default: all)")
    replay.add_argument("--last", type=int, help="only the last N hands")
    replay.add_argument("-r", "--reasoning", action="store_true", help="include private reasoning")

    imp = sub.add_parser(
        "import",
        help="convert site hand-history exports (PokerStars, GGPoker, 888poker text) to JSONL",
    )
    imp.add_argument("files", type=Path, nargs="+", help="text files exported by the site")
    imp.add_argument("-o", "--out", type=Path, required=True, help="JSONL file to write")
    imp.add_argument("--append", action="store_true", help="append instead of overwriting")

    coach = sub.add_parser("coach", help="find one player's recurring leaks in a JSONL file")
    coach.add_argument("file", type=Path)
    coach.add_argument(
        "-p", "--player", help="seat name to coach (default: the hero of imported hands)"
    )
    coach.add_argument("--samples", type=int, default=300, help="equity samples per decision")
    coach.add_argument("--top", type=int, default=5, help="leaks to show")
    coach.add_argument("--json", type=Path, help="also write the full report (with facts) here")
    coach.add_argument(
        "--narrate",
        action="store_true",
        help="ask Claude to explain the leaks in plain language (needs ANTHROPIC_API_KEY)",
    )
    coach.add_argument("--model", default=None, help="model for --narrate (default claude-opus-5)")
    coach.add_argument("--since", help="only hands played on/after this date (YYYY-MM-DD)")
    coach.add_argument("--until", help="only hands played before this date (YYYY-MM-DD)")
    coach.add_argument(
        "--margins",
        default="",
        help=(
            "postflop thresholds, e.g. call=0.05,fold=0.1,strong=0.85,bluff_share=0.3 "
            "(defaults: call 0.03, fold 0.05, strong 0.8, bluff_share 0.25, float_share 0.15)"
        ),
    )

    data = sub.add_parser(
        "dataset", help="export decisions as labelled training data (for a Laya seat, say)"
    )
    data.add_argument("file", type=Path)
    data.add_argument("-o", "--out", type=Path, required=True, help="JSONL file to write")
    data.add_argument("-p", "--player", help="seat to export (default: the hero of imported hands)")
    data.add_argument(
        "--source",
        choices=("coach", "policy", "both"),
        default="coach",
        help="coach = what the charts and the maths say was right; policy = what the seat did",
    )
    data.add_argument("--samples", type=int, default=200, help="equity samples per decision")

    drill = sub.add_parser("drill", help="quiz yourself on the spots the coach flagged")
    drill.add_argument("file", type=Path)
    drill.add_argument(
        "-p", "--player", help="seat name to drill (default: the hero of imported hands)"
    )
    drill.add_argument("-n", "--count", type=int, default=10, help="spots per session")
    drill.add_argument("--samples", type=int, default=300, help="equity samples per decision")

    serve = sub.add_parser("serve", help="open the replay viewer and leaderboard in a browser")
    serve.add_argument("file", type=Path, help="JSONL file (may still be growing)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="open the browser")
    serve.add_argument(
        "--live",
        action="store_true",
        help="also play a session in the background and stream it to the page",
    )
    serve.add_argument("-n", "--hands", type=int, default=100, help="hands to play with --live")
    serve.add_argument("-s", "--seats", default=DEFAULT_SEATS, help="seats for --live ('human' ok)")
    serve.add_argument("--seed", type=int, default=0)
    serve.add_argument("--blinds", default="1/2")
    serve.add_argument("--stack", type=int, default=200)
    serve.add_argument(
        "--pace",
        type=float,
        default=0.8,
        help="seconds between bot decisions with --live, so hands can be watched (default 0.8)",
    )
    return parser


def parse_blinds(text: str) -> tuple[int, int]:
    small, sep, big = text.partition("/")
    if not sep:
        raise ValueError("blinds must look like 1/2")
    return int(small), int(big)


def parse_levels(text: str) -> tuple[Level, ...]:
    """``"1/2,2/4,5/10+1"`` -> a blind schedule; ``+n`` on a level is its ante."""
    levels = []
    for part in text.split(","):
        stakes, _, ante = part.strip().partition("+")
        small, big = parse_blinds(stakes)
        levels.append(Level(small, big, int(ante) if ante else 0))
    if not levels:
        raise ValueError("a schedule needs at least one level, e.g. 1/2")
    return tuple(levels)


def hand_writer(args: argparse.Namespace, out, *, every_hand: bool):
    """The per-hand callback both session shapes share: append to disk, print as asked."""
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.touch()

    def on_hand(history: HandHistory) -> None:
        if args.out is not None:
            write_jsonl(args.out, [history], append=True)
        if every_hand:
            print(history.render(reasoning=args.show), file=out)
            print(file=out)
        elif not args.quiet and int(history.hand_id) % 100 == 0:
            print(f"... {history.hand_id} hands", file=out)

    return on_hand


def cmd_play(args: argparse.Namespace, out) -> int:
    specs = [s for s in args.seats.split(",") if s.strip()]
    agents = make_agents(specs, seed=args.seed)
    check_seats_ready(agents)
    if args.tournament:
        return play_tournament(args, agents, out)

    small, big = parse_blinds(args.blinds or "1/2")
    config = LeagueConfig(
        hands=args.hands if args.hands is not None else 100,
        small_blind=small,
        big_blind=big,
        ante=args.ante or 0,
        buy_in=args.stack,
        top_up=not args.carry,
        seed=args.seed,
    )
    human_seated = any(isinstance(a, HumanAgent) for a in agents)
    on_hand = hand_writer(args, out, every_hand=args.show or human_seated)
    result = run_league(agents, config, on_hand=on_hand)
    if not args.quiet:
        stakes = f"blinds {small}/{big}" + (f" ante {config.ante}" if config.ante else "")
        header = f"{config.hands} hands · {stakes} · buy-in {config.buy_in}"
        print(f"{header} · seats: {seat_list(agents)}", file=out)
        if args.carry:
            print("rebuys: " + ", ".join(f"{k}={v}" for k, v in result.rebuys.items()), file=out)
    print(format_table(result.stats), file=out)
    for line in model_seat_notes(agents):
        print(line, file=out)
    if args.out is not None and not args.quiet:
        print(f"hand histories appended to {args.out}", file=out)
    return 0


def seat_list(agents) -> str:
    return ", ".join(f"{a.name} ({type(a).__name__})" for a in agents)


def play_tournament(args: argparse.Namespace, agents, out) -> int:
    config = TournamentConfig(
        starting_stack=args.stack,
        schedule=parse_levels(args.levels) if args.levels else DEFAULT_SCHEDULE,
        hands_per_level=args.level_hands,
        max_hands=args.hands if args.hands is not None else 5000,
        seed=args.seed,
    )
    if args.blinds or args.ante:
        print("note: a tournament takes its blinds and antes from --levels", file=out)
    human_seated = any(isinstance(a, HumanAgent) for a in agents)
    on_hand = hand_writer(args, out, every_hand=args.show or human_seated)
    result = run_tournament(agents, config, on_hand=on_hand)
    if not args.quiet:
        first, last = config.schedule[0], config.schedule[-1]
        print(
            f"freezeout · {config.starting_stack} chips each · levels {first} to {last}, "
            f"{config.hands_per_level} hands each · seats: {seat_list(agents)}",
            file=out,
        )
    print(result.render(), file=out)
    if not args.quiet:
        print(file=out)
        print(format_table(result.stats), file=out)
    for line in model_seat_notes(agents):
        print(line, file=out)
    if args.out is not None and not args.quiet:
        print(f"hand histories appended to {args.out}", file=out)
    return 0


def check_seats_ready(agents) -> None:
    """Fail before the first hand if a seat cannot run, rather than folding its way through."""
    from poker_table.agents.laya import INSTALL_HINT, LayaAgent

    for agent in agents:
        if isinstance(agent, LayaAgent) and not agent.loadable:
            raise ValueError(f"seat {agent.name!r}: {INSTALL_HINT}")


def model_seat_notes(agents) -> list[str]:
    """What each model-backed seat actually did — a gated seat's row is its fallback's row."""
    from poker_table.agents.laya import LayaAgent

    notes = []
    for agent in agents:
        if not isinstance(agent, LayaAgent):
            continue
        u = agent.usage
        asked = u.decisions + u.gated
        if not asked:
            if u.errors:
                notes.append(f"{agent.name}: every decision failed ({u.errors} errors)")
            continue
        note = (
            f"{agent.name}: {u.decisions}/{asked} decisions were the model's "
            f"({u.gated} handed to the chart below {agent.confidence:.0%} confidence"
            + (f", {u.errors} errors" if u.errors else "")
            + ")"
        )
        if u.avg_latency_ms:
            note += f", {u.avg_latency_ms:.0f} ms each"
        notes.append(note)
    return notes


def cmd_stats(args: argparse.Namespace, out) -> int:
    histories = list(read_jsonl(args.file))
    print(f"{len(histories)} hands from {args.file}", file=out)
    print(format_table(compute_stats(histories)), file=out)
    return 0


def cmd_replay(args: argparse.Namespace, out) -> int:
    histories = list(read_jsonl(args.file))
    if args.hand is not None:
        histories = [h for h in histories if h.hand_id == args.hand]
        if not histories:
            print(f"no hand with id {args.hand!r} in {args.file}", file=sys.stderr)
            return 1
    if args.last is not None:
        histories = histories[-args.last :]
    for history in histories:
        print(history.render(reasoning=args.reasoning), file=out)
        print(file=out)
    return 0


def cmd_import(args: argparse.Namespace, out) -> int:
    from poker_table.coach.importers import import_files

    total = 0
    skipped: list[tuple[str, str]] = []
    heroes: set[str] = set()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    first = not args.append
    for path, result in import_files(args.files):
        write_jsonl(args.out, result.hands, append=not first)
        first = False
        total += len(result.hands)
        skipped += result.skipped
        if result.hero:
            heroes.add(result.hero)
        print(f"{path}: {len(result.hands)} hands, {len(result.skipped)} skipped", file=out)
    for hand_id, reason in skipped[:10]:
        print(f"  skipped #{hand_id}: {reason}", file=out)
    if len(skipped) > 10:
        print(f"  ... and {len(skipped) - 10} more", file=out)
    print(f"{total} hands written to {args.out}", file=out)
    if heroes:
        who = ", ".join(sorted(heroes))
        print(f"next: uv run poker-table coach {args.out} --player {who}", file=out)
    return 0


def resolve_player(histories, player: str | None, path: Path) -> str:
    """The seat to coach: the one asked for, else the hero every imported hand agrees on."""
    names = sorted({p.name for h in histories for p in h.players})
    if player is not None:
        if player not in names:
            raise ValueError(f"no seat named {player!r} in {path}; seats: {', '.join(names)}")
        return player
    heroes = {h.hero for h in histories if h.hero}
    if len(heroes) == 1:
        return heroes.pop()
    if not heroes:
        raise ValueError(
            f"these hands have no hero; pick one with --player (seats: {', '.join(names)})"
        )
    raise ValueError(
        f"several heroes in {path} ({', '.join(sorted(heroes))}); pick one with --player"
    )


def cmd_coach(args: argparse.Namespace, out) -> int:
    import json

    from poker_table.coach.facts import Margins, tag_hands
    from poker_table.coach.report import build_report

    histories = filter_by_date(list(read_jsonl(args.file)), args.since, args.until)
    if not histories:
        raise ValueError("no hands in that date range (imported hands need a header date)")
    player = resolve_player(histories, args.player, args.file)
    margins = Margins.parse(args.margins)
    facts = tag_hands(histories, player, samples=args.samples, margins=margins)
    report = build_report(histories, player, facts)
    print(report.render(top=args.top), file=out)
    if args.narrate:
        from poker_table.agents.llm import DEFAULT_MODEL
        from poker_table.coach.narrate import narrate

        narration = narrate(report, model=args.model or DEFAULT_MODEL, top=args.top)
        print("\nCoach's notes", file=out)
        print(narration.render() or "(the model returned nothing usable)", file=out)
        suffix = f", {narration.dropped} unsupported note(s) dropped" if narration.dropped else ""
        print(f"[{narration.model}, ${narration.cost_usd:.4f}{suffix}]", file=out)
    if args.json is not None:
        args.json.write_text(json.dumps(report.to_dict(), indent=1))
        print(f"\nfull report written to {args.json}", file=out)
    return 0


def cmd_dataset(args: argparse.Namespace, out) -> int:
    from poker_table.coach import dataset
    from poker_table.coach.facts import tag_hands

    histories = list(read_jsonl(args.file))
    player = resolve_player(histories, args.player, args.file)
    samples: list[dataset.Sample] = []
    if args.source in ("coach", "both"):
        facts = tag_hands(histories, player, samples=args.samples)
        samples += list(dataset.from_coach(histories, facts))
    if args.source in ("policy", "both"):
        samples += list(dataset.from_policy(histories, player))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_jsonl(args.out, samples)
    print(f"{player}: {dataset.summarize(samples)}", file=out)
    print(f"written to {args.out}", file=out)
    return 0


def cmd_drill(args: argparse.Namespace, out) -> int:
    from poker_table.coach.drills import DrillLog, run_drill, spots_from
    from poker_table.coach.facts import tag_hands

    histories = list(read_jsonl(args.file))
    player = resolve_player(histories, args.player, args.file)
    facts = tag_hands(histories, player, samples=args.samples)
    spots = spots_from(histories, facts)
    log = DrillLog.load(args.file.with_suffix(f".{player}.drills.json"))
    run_drill(spots, log, count=args.count, output_fn=lambda s: print(s, file=out))
    return 0


def cmd_serve(args: argparse.Namespace, out) -> int:
    import uvicorn

    from poker_table.web.app import create_app

    live = None
    if args.live:
        from poker_table.league import LeagueConfig
        from poker_table.web.live import LiveSession, WebHumanAgent

        small, big = parse_blinds(args.blinds)
        specs = [s for s in args.seats.split(",") if s.strip()]
        agents = [
            WebHumanAgent(a.name) if isinstance(a, HumanAgent) else a
            for a in make_agents(specs, seed=args.seed)
        ]
        check_seats_ready(agents)
        config = LeagueConfig(
            hands=args.hands, small_blind=small, big_blind=big, buy_in=args.stack, seed=args.seed
        )
        args.file.parent.mkdir(parents=True, exist_ok=True)
        args.file.touch()
        live = LiveSession(agents, config, args.file, pace=max(0.0, args.pace))
    app = create_app(args.file, live)
    url = f"http://{args.host}:{args.port}/"
    print(f"poker-table viewer on {url} (Ctrl-C to stop)", file=out)
    if live is not None:
        seats = ", ".join(a.name for a in live.agents)
        print(f"live: {args.hands} hands between {seats}, appending to {args.file}", file=out)
        live.start()
    if args.open:
        import webbrowser

        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: Sequence[str] | None = None, out=None) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    try:
        match args.command:
            case "play":
                return cmd_play(args, out)
            case "stats":
                return cmd_stats(args, out)
            case "replay":
                return cmd_replay(args, out)
            case "serve":
                return cmd_serve(args, out)
            case "import":
                return cmd_import(args, out)
            case "coach":
                return cmd_coach(args, out)
            case "dataset":
                return cmd_dataset(args, out)
            case "drill":
                return cmd_drill(args, out)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
