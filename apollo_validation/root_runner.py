from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import sys
from typing import TextIO

from .context import inspect_context
from .evidence import append_record, now, run_log, write_json, write_reports
from .listing import run_list
from .run_inputs import capture_run_inputs
from .root_cli import RootOptions, parse_root_args, print_help
from .runner import run_category
from .selection import (
    SelectionError,
    prepare_selection,
    selected_test_environment,
    write_selection_evidence,
)


def _run_dir(root: Path, args: RootOptions) -> Path:
    if args.out_dir is not None:
        return args.out_dir if args.out_dir.is_absolute() else root / args.out_dir
    resolved_stamp = args.stamp or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if args.test_profile:
        resolved_stamp = (
            f"{resolved_stamp}-{args.backend}-{args.image_profile}-"
            f"{args.test_profile}"
        )
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


def _replace_latest_link(link: Path, run_dir: Path) -> None:
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.exists():
        return
    link.symlink_to(run_dir.name)


def _update_latest(root: Path, run_dir: Path, summary: dict | None = None) -> None:
    tests_root = root / "build/tests"
    if not _path_is_relative_to(run_dir.resolve(), tests_root.resolve()):
        return
    latest = tests_root / "latest"
    latest.parent.mkdir(parents=True, exist_ok=True)
    _replace_latest_link(latest, run_dir)
    if summary is None:
        return
    backend = summary.get("backend")
    image_profile = summary.get("image_profile")
    test_profile = summary.get("test_profile")
    if all(isinstance(value, str) and value for value in (
        backend,
        image_profile,
        test_profile,
    )):
        _replace_latest_link(
            tests_root / f"latest-{backend}-{image_profile}-{test_profile}",
            run_dir,
        )


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
    summary, exit_code = write_reports(run_dir)
    summary_path = run_dir / "summary.json"
    _update_latest(root, run_dir, summary)
    print(f"RESULT: {summary['status']}")
    print(f"SUMMARY: {_relative_to_root(root, summary_path)}")
    print(f"REPORT: {_relative_to_root(root, run_dir / 'summary.txt')}")
    print(f"JUNIT: {_relative_to_root(root, run_dir / 'junit.xml')}")
    print(f"LOGS: {_relative_to_root(root, run_dir / 'logs')}")
    return exit_code


def _write_context(root: Path, args: RootOptions, run_dir: Path) -> int:
    run_log("START context")
    context = inspect_context(root, args.build_dir, args.machine, args.image)
    context.update(
        {
            "backend": args.backend,
            "image_profile": args.image_profile,
            "test_profile": args.test_profile,
        }
    )
    input_manifest_path = capture_run_inputs(root, run_dir, context)
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
            "artifacts": [
                {"kind": "manifest", "path": str(manifest_path)},
                {"kind": "input_manifest", "path": str(input_manifest_path)},
            ],
            "blockers": context.get("blockers", []),
        },
    )
    run_log(f"DONE context ({status})")
    return 2 if status == "blocked" else 0


def _acquire_lock(root: Path, run_dir: Path) -> tuple[int, TextIO | None]:
    run_log("START lock")
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
        run_log("DONE lock (blocked)")
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
    run_log("DONE lock (pass)")
    return 0, lock_file


def _run_category(root: Path, args: RootOptions, run_dir: Path, label: str) -> int:
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
    run_log(f"START category-{label}")
    rc = run_category(category_args)
    status = "pass" if rc == 0 else "blocked" if rc == 2 else "fail"
    run_log(f"DONE category-{label} ({status})")
    return rc


def run_root_compat(root: Path, argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        return print_help(root)
    try:
        args = parse_root_args(argv)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 64
        return 0 if code == 0 else 64

    run_dir = _run_dir(root, args)
    rejection = _validate_request(root, args.build_dir, run_dir)
    if rejection is not None:
        print(f"error: {rejection}", file=sys.stderr)
        return 64
    try:
        selection, selected_args = prepare_selection(root, args)
    except SelectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 64
    if args.tui:
        from .tui import run_tui

        return run_tui(root, argv, run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ["APOLLO_RUN_TEST_LOG"] = str(run_dir / "logs/runner.log")
    run_log("Environment")
    run_log(f"  root: {root}")
    run_log(f"  build_dir: {args.build_dir}")
    run_log(f"  machine: {args.machine}")
    run_log(f"  image: {args.image}")
    run_log("  mode: headless")
    display_category = selection.category if selection is not None else args.category
    if args.list_suites and not args.category_requested:
        display_category = "all"
    run_log(f"  category: {display_category}")
    if selection is not None:
        run_log(f"  test: {selection.requested[0]}")
        run_log(f"  test_order: {' -> '.join(selection.ordered_tests)}")
    run_log(f"  run_dir: {_relative_to_root(root, run_dir)}")
    run_log(f"  timeout_oeqa: {selected_args.timeout_oeqa}")
    run_log(f"  timeout_fvp: {selected_args.timeout_fvp}")

    context_build_dir = args.build_dir
    requested_conf = root / args.build_dir / "conf/local.conf"
    effective_args = selected_args
    if (args.dry_run or args.list_suites) and not requested_conf.is_file():
        context_build_dir = Path("build")
        effective_args = replace(selected_args, build_dir=context_build_dir)
    try:
        context_rc = _write_context(
            root,
            replace(effective_args, build_dir=context_build_dir),
            run_dir,
        )
    except OSError:
        return _write_internal_result(root, run_dir, "blocked_command_record_init_failed", 70)
    if context_rc != 0:
        return _print_result(root, run_dir)
    if selection is not None:
        write_selection_evidence(run_dir, selection)

    if args.list_suites:
        list_category = args.category if args.category_requested else None
        run_list(run_dir, list_category)
        return _print_result(root, run_dir)

    lock_handle: TextIO | None = None
    if effective_args.category in {"basic", "functional", "power"} and (
        args.preflight_only or not (args.dry_run or args.skip_runtime)
    ):
        lock_rc, lock_handle = _acquire_lock(root, run_dir)
        if lock_rc != 0:
            return _print_result(root, run_dir)
    try:
        try:
            with selected_test_environment(selection):
                _run_category(root, effective_args, run_dir, display_category)
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
            run_log(f"DONE category-{effective_args.category} (blocked)")
    finally:
        if lock_handle is not None:
            lock_handle.close()
    return _print_result(root, run_dir)
