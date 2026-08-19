from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .evidence import write_json, write_reports
from .root_runner import run_root_compat
from .runner import run_category, run_context
from .suites import DEFAULT_PROFILE, list_suites


def _write_or_print(data: dict, out: Path | None, fmt: str = "json") -> None:
    if out is not None:
        write_json(out, data)
        print(out)
        return
    if fmt == "json":
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for category, entries in data.get("categories", {}).items():
            print(f"{category}: {len(entries)}")


def cmd_context(args: argparse.Namespace) -> int:
    return run_context(args.root, args.build_dir, args.machine, args.out)


def cmd_list(args: argparse.Namespace) -> int:
    data = list_suites(profile=args.profile, category=args.category)
    _write_or_print(data, args.out, args.format)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    return run_category(args)


def cmd_summarize(args: argparse.Namespace) -> int:
    summary, exit_code = write_reports(args.run_dir)
    if args.out != args.run_dir / "summary.json":
        write_json(args.out, summary)
    print(args.out)
    return exit_code


def cmd_root_run(args: argparse.Namespace) -> int:
    rest = list(args.args)
    if rest and rest[0] == "--":
        rest = rest[1:]
    return run_root_compat(args.root.resolve(), rest)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apollo FVP validation runner")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    context = subparsers.add_parser("context", help="Inspect Apollo FVP context")
    context.add_argument("--root", type=Path, default=Path("."))
    context.add_argument("--build-dir", type=Path, default=Path("build"))
    context.add_argument("--machine", default="apollo-fvp")
    context.add_argument("--out", type=Path, required=True)
    context.set_defaults(func=cmd_context)

    list_cmd = subparsers.add_parser("list", help="List validation suites")
    list_cmd.add_argument("--profile", default=DEFAULT_PROFILE)
    list_cmd.add_argument(
        "--category",
        choices=("basic", "functional", "power", "extended", "stress"),
        default=None,
    )
    list_cmd.add_argument("--format", choices=("json", "text"), default="text")
    list_cmd.add_argument("--out", type=Path)
    list_cmd.set_defaults(func=cmd_list)

    run = subparsers.add_parser("run", help="Run a validation category")
    run.add_argument("--category", choices=("basic", "functional", "power", "extended", "stress"), required=True)
    run.add_argument("--root", type=Path, default=Path("."))
    run.add_argument("--build-dir", type=Path, default=Path("build"))
    run.add_argument("--machine", default="apollo-fvp")
    run.add_argument("--image", default="nexios-image")
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--timeout-oeqa", type=int, default=10800)
    run.add_argument("--out-dir", type=Path, required=True)
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run)

    summarize = subparsers.add_parser("summarize", help="Summarize a run directory")
    summarize.add_argument("--run-dir", type=Path, required=True)
    summarize.add_argument("--out", type=Path, required=True)
    summarize.set_defaults(func=cmd_summarize)

    root_run = subparsers.add_parser("root-run", help="Compatibility entry for top-level run_test.sh")
    root_run.add_argument("--root", type=Path, default=Path("."))
    root_run.add_argument("args", nargs=argparse.REMAINDER)
    root_run.set_defaults(func=cmd_root_run)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
