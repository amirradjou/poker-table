"""Command-line entry point (`poker-table`)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from poker_table import __version__
from poker_table.agents.human import HumanAgent
from poker_table.agents.registry import available_kinds, make_agents
from poker_table.history import HandHistory, read_jsonl, write_jsonl
from poker_table.league import LeagueConfig, run_league
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
    play.add_argument("-n", "--hands", type=int, default=100, help="hands to play (default 100)")
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
    play.add_argument("--blinds", default="1/2", help="small/big blind, e.g. 1/2 (default)")
    play.add_argument("--stack", type=int, default=200, help="buy-in in chips (default 200)")
    play.add_argument(
        "--carry", action="store_true", help="carry stacks between hands and rebuy when busted"
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
        "import", help="convert site hand-history exports (PokerStars text) to JSONL"
    )
    imp.add_argument("files", type=Path, nargs="+", help="text files exported by the site")
    imp.add_argument("-o", "--out", type=Path, required=True, help="JSONL file to write")
    imp.add_argument("--append", action="store_true", help="append instead of overwriting")

    coach = sub.add_parser("coach", help="find one player's recurring leaks in a JSONL file")
    coach.add_argument("file", type=Path)
    coach.add_argument("-p", "--player", required=True, help="seat name to coach")
    coach.add_argument("--samples", type=int, default=300, help="equity samples per decision")
    coach.add_argument("--top", type=int, default=5, help="leaks to show")
    coach.add_argument("--json", type=Path, help="also write the full report (with facts) here")
    coach.add_argument(
        "--narrate",
        action="store_true",
        help="ask Claude to explain the leaks in plain language (needs ANTHROPIC_API_KEY)",
    )
    coach.add_argument("--model", default=None, help="model for --narrate (default claude-opus-5)")

    drill = sub.add_parser("drill", help="quiz yourself on the spots the coach flagged")
    drill.add_argument("file", type=Path)
    drill.add_argument("-p", "--player", required=True, help="seat name to drill")
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
    return parser


def parse_blinds(text: str) -> tuple[int, int]:
    small, sep, big = text.partition("/")
    if not sep:
        raise ValueError("blinds must look like 1/2")
    return int(small), int(big)


def cmd_play(args: argparse.Namespace, out) -> int:
    small, big = parse_blinds(args.blinds)
    specs = [s for s in args.seats.split(",") if s.strip()]
    agents = make_agents(specs, seed=args.seed)
    config = LeagueConfig(
        hands=args.hands,
        small_blind=small,
        big_blind=big,
        buy_in=args.stack,
        top_up=not args.carry,
        seed=args.seed,
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.touch()

    human_seated = any(isinstance(a, HumanAgent) for a in agents)

    def on_hand(history: HandHistory) -> None:
        if args.out is not None:
            write_jsonl(args.out, [history], append=True)
        if args.show or human_seated:
            print(history.render(reasoning=args.show), file=out)
            print(file=out)
        elif not args.quiet and int(history.hand_id) % 100 == 0:
            print(f"... {history.hand_id} hands", file=out)

    result = run_league(agents, config, on_hand=on_hand)
    if not args.quiet:
        seats = ", ".join(f"{a.name} ({type(a).__name__})" for a in agents)
        header = f"{config.hands} hands · blinds {small}/{big} · buy-in {config.buy_in}"
        print(f"{header} · seats: {seats}", file=out)
        if args.carry:
            print("rebuys: " + ", ".join(f"{k}={v}" for k, v in result.rebuys.items()), file=out)
    print(format_table(result.stats), file=out)
    if args.out is not None and not args.quiet:
        print(f"hand histories appended to {args.out}", file=out)
    return 0


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


def cmd_coach(args: argparse.Namespace, out) -> int:
    import json

    from poker_table.coach.facts import tag_hands
    from poker_table.coach.report import build_report

    histories = list(read_jsonl(args.file))
    if not any(p.name == args.player for h in histories for p in h.players):
        names = sorted({p.name for h in histories for p in h.players})
        raise ValueError(f"no seat named {args.player!r} in {args.file}; seats: {', '.join(names)}")
    facts = tag_hands(histories, args.player, samples=args.samples)
    report = build_report(histories, args.player, facts)
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


def cmd_drill(args: argparse.Namespace, out) -> int:
    from poker_table.coach.drills import DrillLog, run_drill, spots_from
    from poker_table.coach.facts import tag_hands

    histories = list(read_jsonl(args.file))
    facts = tag_hands(histories, args.player, samples=args.samples)
    spots = spots_from(histories, facts)
    log = DrillLog.load(args.file.with_suffix(f".{args.player}.drills.json"))
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
        config = LeagueConfig(
            hands=args.hands, small_blind=small, big_blind=big, buy_in=args.stack, seed=args.seed
        )
        args.file.parent.mkdir(parents=True, exist_ok=True)
        args.file.touch()
        live = LiveSession(agents, config, args.file)
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
            case "drill":
                return cmd_drill(args, out)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
