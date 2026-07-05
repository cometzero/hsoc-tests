from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
from time import monotonic
from typing import Any

from .context import inspect_context
from .evidence import append_record, now, summarize_records, write_json
from .suites import list_suites


JsonObject = dict[str, Any]


def _rel(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return str(path)


def _run_subprocess(argv: list[str], root: Path, stdout_log: Path, stderr_log: Path) -> int:
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    with stdout_log.open("w", encoding="utf-8") as stdout, stderr_log.open(
        "w", encoding="utf-8"
    ) as stderr:
        proc = subprocess.run(
            argv,
            cwd=root,
            check=False,
            text=True,
            stdout=stdout,
            stderr=stderr,
        )
    return proc.returncode


def _record_command(
    commands_file: Path,
    name: str,
    argv: list[str],
    status: str,
    started_at: str,
    duration_s: float,
    exit_code: int | None = None,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
    artifacts: list[JsonObject] | None = None,
    blockers: list[JsonObject] | None = None,
) -> None:
    record: JsonObject = {
        "name": name,
        "argv": argv,
        "status": status,
        "started_at": started_at,
        "finished_at": now(),
        "duration_s": round(duration_s, 6),
        "required": True,
    }
    if exit_code is not None:
        record["exit_code"] = exit_code
    if stdout_log is not None:
        record["stdout_log"] = str(stdout_log)
    if stderr_log is not None:
        record["stderr_log"] = str(stderr_log)
    if artifacts:
        record["artifacts"] = artifacts
    if blockers:
        record["blockers"] = blockers
    append_record(commands_file, record)


def _write_summary(run_dir: Path) -> int:
    summary, exit_code = summarize_records(run_dir)
    write_json(run_dir / "summary.json", summary)
    return exit_code


def run_context(root: Path, build_dir: Path, machine: str, out: Path) -> int:
    result = inspect_context(root, build_dir, machine)
    write_json(out, result)
    return 2 if result.get("status") == "blocked" else 0


def run_basic(
    root: Path,
    build_dir: Path,
    machine: str,
    timeout: int,
    out_dir: Path,
    dry_run: bool,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    commands_file = out_dir / "commands.jsonl"
    preflight_json = out_dir / "preflight.json"
    preflight_stdout = out_dir / "logs/preflight.stdout.log"
    preflight_stderr = out_dir / "logs/preflight.stderr.log"
    preflight_argv = [
        sys.executable,
        "scripts/test/run_test_manifest.py",
        "preflight",
        "--build-dir",
        str(build_dir),
        "--machine",
        machine,
        "--out",
        str(preflight_json),
    ]
    started = now()
    start = monotonic()
    preflight_rc = _run_subprocess(preflight_argv, root, preflight_stdout, preflight_stderr)
    preflight_data: JsonObject = {}
    if preflight_json.is_file():
        preflight_data = __import__("json").loads(preflight_json.read_text(encoding="utf-8"))
    if preflight_rc != 0:
        _record_command(
            commands_file,
            "basic-preflight",
            preflight_argv,
            "blocked",
            started,
            monotonic() - start,
            preflight_rc,
            preflight_stdout,
            preflight_stderr,
            artifacts=[{"kind": "preflight", "path": str(preflight_json)}],
            blockers=preflight_data.get("blockers", []),
        )
        _write_summary(out_dir)
        return 2
    _record_command(
        commands_file,
        "basic-preflight",
        preflight_argv,
        "pass",
        started,
        monotonic() - start,
        preflight_rc,
        preflight_stdout,
        preflight_stderr,
        artifacts=[{"kind": "preflight", "path": str(preflight_json)}],
    )

    fvp_out = out_dir / "fvp"
    basic_argv = [
        sys.executable,
        "scripts/run/runfvp_log_boot.py",
        "--machine",
        machine,
        "--timeout",
        str(timeout),
        "--out-dir",
        str(fvp_out),
        "--require",
        "all",
        "--no-login",
    ]
    if dry_run:
        _record_command(
            commands_file,
            "basic-boot",
            basic_argv,
            "skipped",
            now(),
            0.0,
            artifacts=[{"kind": "planned_command", "argv": basic_argv}],
        )
        return _write_summary(out_dir)

    stdout_log = out_dir / "logs/basic-boot.stdout.log"
    stderr_log = out_dir / "logs/basic-boot.stderr.log"
    started = now()
    start = monotonic()
    rc = _run_subprocess(basic_argv, root, stdout_log, stderr_log)
    result_json = fvp_out / "result.json"
    status = "pass" if rc == 0 else "fail"
    if rc == 2:
        status = "blocked"
    _record_command(
        commands_file,
        "basic-boot",
        basic_argv,
        status,
        started,
        monotonic() - start,
        rc,
        stdout_log,
        stderr_log,
        artifacts=[
            {"kind": "fvp_result", "path": str(result_json)},
            {"kind": "fvp_summary", "path": str(fvp_out / "summary.txt")},
        ],
    )
    return _write_summary(out_dir)


def run_functional_dry_run(root: Path, build_dir: Path, machine: str, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    commands_file = out_dir / "commands.jsonl"
    context = inspect_context(root, build_dir, machine)
    write_json(out_dir / "context.json", context)
    oeqa_conf = out_dir / "conf/oeqa-functional.conf"
    oeqa_cmd = [
        "bash",
        "-lc",
        "source layers/poky/oe-init-build-env "
        + shlex.quote(str(build_dir))
        + " >/dev/null && bitbake -R "
        + shlex.quote(str(oeqa_conf))
        + " nexios-image -c testimage",
    ]
    pytest_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "sw-ref-stack/test_automation/tests/test_bsp_demos",
        "--platform",
        "fvp_rd_aspen",
        "--config",
        "sw-ref-stack/test_automation/test_automation/configs/baremetal_config.yaml",
        "--build-dir",
        str(Path(context.get("fvpconf", {}).get("path", "")).parent),
    ]
    _record_command(
        commands_file,
        "functional-oeqa",
        oeqa_cmd,
        "skipped",
        now(),
        0.0,
        artifacts=[{"kind": "planned_command", "argv": oeqa_cmd}],
    )
    _record_command(
        commands_file,
        "functional-sw-ref-stack-pytest",
        pytest_cmd,
        "skipped",
        now(),
        0.0,
        artifacts=[{"kind": "planned_command", "argv": pytest_cmd}],
    )
    return _write_summary(out_dir)


def run_category(args: Any) -> int:
    root = args.root.resolve()
    build_dir = args.build_dir
    out_dir = args.out_dir
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    if args.category == "basic":
        return run_basic(root, build_dir, args.machine, args.timeout, out_dir, args.dry_run)
    if args.category == "functional":
        if not args.dry_run:
            print("functional runtime is intentionally opt-in through OEQA; use --dry-run first", file=sys.stderr)
            return 2
        return run_functional_dry_run(root, build_dir, args.machine, out_dir)
    data = list_suites(category=args.category)
    write_json(out_dir / "suite.json", data)
    commands_file = out_dir / "commands.jsonl"
    _record_command(
        commands_file,
        f"{args.category}-list",
        [sys.executable, "-m", "apollo_validation.cli", "list", "--category", args.category],
        "skipped",
        now(),
        0.0,
        artifacts=[{"kind": "suite", "path": str(out_dir / "suite.json")}],
    )
    return _write_summary(out_dir)
