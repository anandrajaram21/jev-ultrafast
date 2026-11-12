"""`jev` opens the local inspector; `jev run` performs one goal against any site, from any project."""

import argparse
import json
import sys
from pathlib import Path

from . import demo
from .agent import Agent


def run(args):
    cookies = json.loads(Path(args.cookies).read_text()) if args.cookies else None
    if cookies is not None and not isinstance(cookies, list):
        raise SystemExit("--cookies must be a JSON list of CDP cookies")
    state, error = None, None
    with Agent(args.url, args.goal, cookies=cookies) as agent:
        try:
            printed = 0
            for state in agent.run():
                for h in state["history"][printed:]:
                    text = f"  text={h['text']!r}" if h.get("text") else ""
                    print(f"{h['elapsed_ms']:>6} ms  {h['operation']:<9} {h['action']}{text}", flush=True)
                printed = len(state["history"])
        except (RuntimeError, ValueError) as e:
            error = str(e)
        finally:
            state = agent.snapshot()
    if args.trace:
        Path(args.trace).write_text(json.dumps(state, indent=2))
    calls = len(state["decisions"]) + len(state["text_calls"])
    print(
        f"{state['status']} in {state['elapsed_ms'] / 1000:.1f} s · {len(state['history'])} actions · "
        f"{calls} model calls · {state['page']['url']}"
    )
    if error:
        print(f"jev: {error}", file=sys.stderr)
        return 2
    # DONE is the model's claim. Exit 0 only means it claimed success; verify the outcome independently.
    return 0 if state["status"] == "done" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jev", description="Jev Ultrafast browser agent.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("inspect", help="Open the local inspector (the default).")
    runner = commands.add_parser(
        "run",
        help="Perform one goal and exit.",
        description="Exit 0 when the model chose DONE, 1 when it stopped as blocked, 2 on an error. "
        "DONE is not proof: check the outcome independently.",
    )
    runner.add_argument("--url", required=True, help="Starting page.")
    runner.add_argument("--goal", action="append", required=True, help="Repeat for an ordered list of goals.")
    runner.add_argument("--cookies", help="JSON list of CDP cookies to set before the first page load.")
    runner.add_argument("--trace", help="Write the final run state, including every decision, to this JSON file.")
    args = parser.parse_args(argv)
    demo.load_environment()
    if args.command == "run":
        sys.exit(run(args))
    demo.main()
