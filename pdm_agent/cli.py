"""Command line entry point.

    pdm-agent scan                 alarms + trend forecast for every sensor
    pdm-agent workflow [--live]    run alarm -> work order on recent alarms
    pdm-agent status P-101         everything known about one asset
    pdm-agent search "bearing"     search the manuals
    pdm-agent chat                 talk to the Claude-powered agent
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from .domain import WorkOrder
from .plant import Plant
from .sensors import detect_alarms, forecast, load_readings, load_sensor_configs
from .workflow import alarm_to_work_order

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"


def cmd_scan(args) -> None:
    for cfg in load_sensor_configs(args.data):
        readings = load_readings(Path(args.data) / "sensors" / cfg.file)
        alarms = detect_alarms(readings, cfg)
        print(f"\n== {cfg.asset_id} {cfg.parameter} ({len(readings)} readings, "
              f"{readings[0][0]:%Y-%m-%d} to {readings[-1][0]:%Y-%m-%d})")
        print(f"   {forecast(readings, cfg).summary()}")
        print(f"   {len(alarms)} alarm events:")
        for alarm in alarms[-args.last:]:
            print(f"     {alarm.summary()}")


def cmd_workflow(args) -> None:
    plant = Plant(args.data)
    for cfg in load_sensor_configs(args.data):
        readings = load_readings(Path(args.data) / "sensors" / cfg.file)
        since = readings[-1][0] - timedelta(days=args.days)
        for alarm in [a for a in detect_alarms(readings, cfg) if a.timestamp >= since]:
            result = alarm_to_work_order(alarm, plant, sandbox=not args.live)
            print(result.render(), end="\n\n")
    if not args.live:
        print("Sandbox mode: nothing was sent or saved. Re-run with --live to submit.")


def cmd_status(args) -> None:
    from .agent import Toolbox

    toolbox = Toolbox(args.data, confirm=lambda wo: False)
    matches = toolbox.plant.resolve_asset(args.asset)
    if not matches:
        sys.exit(f"No asset matches {args.asset!r}")
    print(json.dumps(toolbox.tool_get_asset_status(matches[0].id), indent=2, default=str))


def cmd_search(args) -> None:
    from .manuals import ManualIndex

    for p in ManualIndex(Path(args.data) / "manuals").search(args.query):
        print(f"--- {p.heading}  [{p.source}]\n{p.text}\n")


def confirm_in_terminal(wo: WorkOrder) -> bool:
    print("\n  Create work order?")
    for key in ("asset_id", "type", "priority", "failure_mode", "description"):
        print(f"    {key:<13} {getattr(wo, key)}")
    return input("  Confirm [y/N]: ").strip().lower() == "y"


def cmd_chat(args) -> None:
    import anthropic

    from .agent import MaintenanceAgent, Toolbox

    agent = MaintenanceAgent(Toolbox(args.data, confirm_in_terminal, sandbox=not args.live))
    mode = "LIVE: work orders are saved" if args.live else "sandbox: work orders are not saved"
    print(f"Maintenance assistant ({mode}). Type 'exit' to quit.")
    questions = [args.question] if args.question else None
    while True:
        if questions is not None:
            if not questions:
                return
            question = questions.pop()
            print(f"\nYou: {question}")
        else:
            try:
                question = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        if question.lower() in {"exit", "quit"}:
            return
        if not question:
            continue
        try:
            print(f"\nAgent: {agent.ask(question)}")
        except anthropic.AuthenticationError:
            sys.exit("Claude API key missing or invalid. Set ANTHROPIC_API_KEY (see .env.example).")
        except TypeError as exc:
            if "authentication" in str(exc).lower():
                sys.exit("No Claude credentials found. Set ANTHROPIC_API_KEY (see .env.example).")
            raise
        except anthropic.APIConnectionError:
            sys.exit("Could not reach the Claude API. Check your internet connection.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pdm-agent", description="AI agent for predictive maintenance")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="data folder (default: bundled demo data)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="detect alarms and forecast trends")
    p.add_argument("--last", type=int, default=10, help="alarms to show per sensor")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("workflow", help="run alarm -> work order")
    p.add_argument("--days", type=int, default=90, help="only alarms from the last N days")
    p.add_argument("--live", action="store_true", help="actually notify and save work orders")
    p.set_defaults(func=cmd_workflow)

    p = sub.add_parser("status", help="everything known about one asset")
    p.add_argument("asset")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="search equipment manuals")
    p.add_argument("query")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("chat", help="talk to the AI maintenance agent")
    p.add_argument("question", nargs="?", help="ask one question and exit")
    p.add_argument("--live", action="store_true", help="save confirmed work orders")
    p.set_defaults(func=cmd_chat)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
