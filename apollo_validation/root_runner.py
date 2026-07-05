from __future__ import annotations

import argparse
import fcntl
from pathlib import Path
import sys
from typing import TextIO

from .context import inspect_context
from .evidence import append_record, now, summarize_records, write_json
from .runner import run_category
from .suites import list_suites


CATEGORIES = ("basic", "functional", "power", "extended", "stress")


def _run_dir(root: Path, stamp: str | None, out_dir: Path | None) -> Path:
    if out_dir is not None:
        return out_dir if out_dir.is_absolute() else root / out_dir
    resolved_stamp = stamp if stamp is not None else now().replace(":", "").replace("-", "")
    return root / "build/tests" / resolved_stamp


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _relative_to_root(root: Path, path: Path) -> str:
    if path.is_absolute():
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)
    return str(path)


def _validate_request(root: Path, build_dir: Path, run_dir: Path) -> str | None:
    root = root.resolve()
    resolved_build = (root / build_dir).resolve() if not build_dir.is_absolute() else build_dir.resolve()
    resolved_run = run_dir.resolve()
    protected_conf = (root / "build/conf").resolve()
    tests_root = (root / "build/tests").resolve()
    if resolved_build == protected_conf or _path_is_relative_to(resolved_build, protected_conf):
        return "protected build directory is not a valid --build-dir"
    if _path_is_relative_to(resolved_run, protected_conf):
        return "protected output directory is not valid"
    if resolved_run == root:
        return "output directory must not be the project root"
    if resolved_run == tests_root or not _path_is_relative_to(resolved_run, tests_root):
        return "output directory is outside build/tests"
    return None


def _update_latest(root: Path, run_dir: Path) -> None:
    tests_root = root / "build/tests"
    if not _path_is_relative_to(run_dir.resolve(), tests_root.resolve()):
        return
    latest = tests_root / "latest"
    latest.parent.mkdir(parents=True, exist_ok=True)
    if latest.is_symlink() or latest.is_file():
        latest.unlink()
    elif latest.exists():
        return
    latest.symlink_to(run_dir.name)


def _write_internal_result(root: Path, run_dir: Path, reason: str, exit_code: int) -> int:
    summary_path = run_dir / "summary.json"
    write_json(
        summary_path,
        {
            "status": "BLOCKED",
            "exit_code": exit_code,
            "run_dir": str(run_dir),
            "records": [],
            "record_count": 0,
            "blockers": [{"reason": reason}],
        },
    )
    _update_latest(root, run_dir)
    print("RESULT: BLOCKED")
    print(f"SUMMARY: {_relative_to_root(root, summary_path)}")
    return exit_code


def _print_result(root: Path, run_dir: Path) -> int:
    summary, exit_code = summarize_records(run_dir)
    summary_path = run_dir / "summary.json"
    write_json(summary_path, summary)
    _update_latest(root, run_dir)
    print(f"RESULT: {summary['status']}")
    print(f"SUMMARY: {_relative_to_root(root, summary_path)}")
    return exit_code


def _print_suites(category: str | None) -> None:
    data = list_suites(category=category)
    for name, entries in data.get("categories", {}).items():
        print(f"{name}:")
        for entry in entries:
            print(f"  {entry.get('name')}")


def _write_context(root: Path, build_dir: Path, machine: str, run_dir: Path) -> int:
    print("[run_test] START context", flush=True)
    context = inspect_context(root, build_dir, machine)
    manifest_path = run_dir / "manifest.json"
    write_json(manifest_path, context)
    status = "blocked" if context.get("status") == "blocked" else "pass"
    append_record(
        run_dir / "commands.jsonl",
        {
            "name": "context",
            "argv": ["apollo_validation.cli", "context"],
            "status": status,
            "started_at": now(),
            "finished_at": now(),
            "required": True,
            "artifacts": [{"kind": "manifest", "path": str(manifest_path)}],
            "blockers": context.get("blockers", []),
        },
    )
    print(f"[run_test] DONE context ({status})", flush=True)
    return 2 if status == "blocked" else 0


def _acquire_lock(root: Path, run_dir: Path) -> tuple[int, TextIO | None]:
    print("[run_test] START lock", flush=True)
    lock_path = root / "build/tests/.run_test.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        append_record(
            run_dir / "commands.jsonl",
            {
                "name": "lock",
                "argv": ["flock", str(lock_path)],
                "status": "blocked",
                "started_at": now(),
                "finished_at": now(),
                "required": True,
                "blockers": [{"reason": "blocked_lock_held"}],
            },
        )
        print("[run_test] DONE lock (blocked)", flush=True)
        lock_file.close()
        return 2, None
    append_record(
        run_dir / "commands.jsonl",
        {
            "name": "lock",
            "argv": ["flock", str(lock_path)],
            "status": "pass",
            "started_at": now(),
            "finished_at": now(),
            "required": True,
        },
    )
    print("[run_test] DONE lock (pass)", flush=True)
    return 0, lock_file


def _parse_root_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apollo FVP validation runner")
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--machine", default="apollo-fvp")
    parser.add_argument("--image", default="nexios-image")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--stamp")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument("--category", choices=CATEGORIES, default="basic")
    parser.add_argument("--include-qbox-runtime", action="store_true")
    parser.add_argument("--timeout-oeqa", type=int, default=10800)
    parser.add_argument("--timeout-fvp", type=int, default=300)
    return parser.parse_args(argv)


def _category_was_requested(argv: list[str]) -> bool:
    return any(arg == "--category" or arg.startswith("--category=") for arg in argv)


def _print_help(root: Path) -> int:
    help_script = root / "scripts/test/run_test_cli.py"
    if help_script.is_file():
        namespace: dict[str, str] = {"__name__": "__run_test_cli__"}
        exec(help_script.read_text(encoding="utf-8"), namespace)
        print(namespace["USAGE"])
        return 0
    print("Usage: ./run_test.sh [options]")
    print("  --category basic|functional|power|extended|stress")
    return 0


def _run_category(root: Path, args: argparse.Namespace, run_dir: Path) -> int:
    category_args = argparse.Namespace(
        category=args.category,
        root=root,
        build_dir=args.build_dir,
        machine=args.machine,
        image=args.image,
        timeout=args.timeout_fvp,
        timeout_oeqa=args.timeout_oeqa,
        out_dir=run_dir,
        dry_run=args.dry_run or args.skip_runtime,
        preflight_only=args.preflight_only,
    )
    print(f"[run_test] START category-{args.category}", flush=True)
    rc = run_category(category_args)
    status = "pass" if rc == 0 else "blocked" if rc == 2 else "fail"
    print(f"[run_test] DONE category-{args.category} ({status})", flush=True)
    return rc


def run_root_compat(root: Path, argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        return _print_help(root)
    category_requested = _category_was_requested(argv)
    try:
        args = _parse_root_args(argv)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 64
        return 0 if code == 0 else 64

    run_dir = _run_dir(root, args.stamp, args.out_dir)
    rejection = _validate_request(root, args.build_dir, run_dir)
    if rejection is not None:
        print(f"error: {rejection}", file=sys.stderr)
        return 64
    run_dir.mkdir(parents=True, exist_ok=True)
    print("[run_test] Environment", flush=True)
    print(f"[run_test]   root: {root}", flush=True)
    print(f"[run_test]   build_dir: {args.build_dir}", flush=True)
    print(f"[run_test]   machine: {args.machine}", flush=True)
    print(f"[run_test]   image: {args.image}", flush=True)
    display_category = "all" if args.list and not category_requested else args.category
    print(f"[run_test]   category: {display_category}", flush=True)
    print(f"[run_test]   run_dir: {_relative_to_root(root, run_dir)}", flush=True)
    print(f"[run_test]   timeout_oeqa: {args.timeout_oeqa}", flush=True)
    print(f"[run_test]   timeout_fvp: {args.timeout_fvp}", flush=True)

    context_build_dir = args.build_dir
    requested_conf = root / args.build_dir / "conf/local.conf"
    if (args.dry_run or args.list) and not requested_conf.is_file():
        context_build_dir = Path("build")
        args.build_dir = context_build_dir
    try:
        context_rc = _write_context(root, context_build_dir, args.machine, run_dir)
    except OSError:
        return _write_internal_result(root, run_dir, "blocked_command_record_init_failed", 70)
    if context_rc != 0:
        return _print_result(root, run_dir)

    if args.list:
        list_category = args.category if category_requested else None
        list_label = args.category if category_requested else "all"
        print(f"[run_test] START category-{list_label}-list", flush=True)
        suite_path = run_dir / "suite.json"
        write_json(suite_path, list_suites(category=list_category))
        _print_suites(list_category)
        record_argv = ["apollo_validation.cli", "list"]
        if list_category is not None:
            record_argv.extend(("--category", list_category))
        append_record(
            run_dir / "commands.jsonl",
            {
                "name": f"category-{list_label}-list",
                "argv": record_argv,
                "status": "pass",
                "started_at": now(),
                "finished_at": now(),
                "required": False,
                "artifacts": [{"kind": "suite", "path": str(suite_path)}],
            },
        )
        print(f"[run_test] DONE category-{list_label}-list (pass)", flush=True)
        return _print_result(root, run_dir)

    lock_handle: TextIO | None = None
    if args.category in {"basic", "functional", "power"} and (
        args.preflight_only or not (args.dry_run or args.skip_runtime)
    ):
        lock_rc, lock_handle = _acquire_lock(root, run_dir)
        if lock_rc != 0:
            return _print_result(root, run_dir)
    try:
        try:
            _run_category(root, args, run_dir)
        except KeyboardInterrupt:
            append_record(
                run_dir / "commands.jsonl",
                {
                    "name": "interrupt",
                    "argv": ["KeyboardInterrupt"],
                    "status": "blocked",
                    "started_at": now(),
                    "finished_at": now(),
                    "required": True,
                    "blockers": [{"reason": "blocked_interrupted"}],
                },
            )
            print(f"[run_test] DONE category-{args.category} (blocked)", flush=True)
    finally:
        if lock_handle is not None:
            lock_handle.close()
    return _print_result(root, run_dir)
