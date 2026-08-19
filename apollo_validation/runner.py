from __future__ import annotations

import os
from pathlib import Path
import shutil
from shlex import quote as shlex_quote
import signal
import subprocess
import time
from time import monotonic
from typing import Any

from .context import inspect_context
from .evidence import append_record, now, run_log as _log, write_json, write_reports
from .suites import list_suites


JsonObject = dict[str, Any]
PYTHON = "python3"
HOST_PYTHON_ENV = "RUN_TEST_HOST_PYTHON_BIN"


def _status_from_rc(rc: int) -> str:
    if rc == 0:
        return "pass"
    if rc == 2:
        return "blocked"
    return "fail"


def _python_has_oeqa_deps(python: Path) -> bool:
    if not python.is_file() or not os.access(python, os.X_OK):
        return False
    result = subprocess.run(
        [str(python), "-c", "import pexpect, ptyprocess"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0


def _host_python_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_python = os.environ.get(HOST_PYTHON_ENV)
    if env_python:
        candidates.append(Path(env_python))
    path_python = shutil.which(PYTHON)
    if path_python:
        candidates.append(Path(path_python))
    candidates.extend((Path("/usr/bin/python3"), Path("/usr/local/bin/python3")))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _select_host_python(out_dir: Path) -> Path | None:
    commands_file = out_dir / "commands.jsonl"
    candidates = _host_python_candidates()
    argv = [str(candidate) for candidate in candidates]
    _log("START host-python")
    started = now()
    start = monotonic()
    for candidate in candidates:
        if not _python_has_oeqa_deps(candidate):
            continue
        _record_command(
            commands_file,
            "host-python",
            [str(candidate), "-c", "import pexpect, ptyprocess"],
            "pass",
            started,
            monotonic() - start,
            0,
        )
        _log(f"DONE host-python (pass {candidate})")
        return candidate

    _record_command(
        commands_file,
        "host-python",
        argv,
        "blocked",
        started,
        monotonic() - start,
        blockers=[
            {
                "reason": "blocked_missing_host_python_pexpect",
                "candidates": argv,
                "hint": f"set {HOST_PYTHON_ENV} to a Python with pexpect and ptyprocess",
            }
        ],
    )
    _log("DONE host-python (blocked)")
    return None


def _cleanup_process_group(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        time.sleep(0.2)


def _read_process_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace")


def _matching_cmdline_pids(tokens: list[str]) -> list[int]:
    if not tokens or not Path("/proc").is_dir():
        return []
    own_pids = {os.getpid(), os.getppid()}
    matched: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in own_pids:
            continue
        cmdline = _read_process_cmdline(pid)
        if cmdline and all(token in cmdline for token in tokens):
            matched.append(pid)
    return matched


def _cleanup_processes_by_cmdline(tokens: list[str]) -> None:
    current_pgid = os.getpgrp()
    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = _matching_cmdline_pids(tokens)
        if not pids:
            return
        groups: set[int] = set()
        for pid in pids:
            try:
                groups.add(os.getpgid(pid))
            except ProcessLookupError:
                continue
        for pgid in groups:
            try:
                if pgid == current_pgid:
                    for pid in pids:
                        os.kill(pid, sig)
                else:
                    os.killpg(pgid, sig)
            except ProcessLookupError:
                continue
        time.sleep(0.2)


def _stop_bitbake_server(root: Path, build_dir: Path, host_python_bin: Path | None) -> None:
    host_python_prefix = ""
    if host_python_bin is not None:
        host_python_prefix = f"export PATH={shlex_quote(str(host_python_bin.parent))}:$PATH && "
    script = (
        f"{host_python_prefix}"
        f"source layers/poky/oe-init-build-env {shlex_quote(str(build_dir))} >/dev/null && "
        "bitbake -m"
    )
    try:
        subprocess.run(
            ["bash", "-lc", script],
            cwd=root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return


def _print_boot_summary(summary_path: Path) -> None:
    if not summary_path.is_file():
        return
    interesting_prefixes = (
        "passed:",
        "boot_status_passed:",
        "duration_s:",
        "  - rse:",
        "  - safety_island_cl0:",
        "  - safety_island_cl1:",
        "  - tf_a:",
        "  - u_boot_linux:",
    )
    for line in summary_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(interesting_prefixes):
            _log(f"PROGRESS basic-boot: {line.strip()}")


def _rel(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return str(path)


def _run_subprocess(
    argv: list[str],
    root: Path,
    stdout_log: Path,
    stderr_log: Path,
    cleanup_cmdline_tokens: list[str] | None = None,
) -> int:
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    with stdout_log.open("w", encoding="utf-8") as stdout, stderr_log.open(
        "w", encoding="utf-8"
    ) as stderr:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=root,
                text=True,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        except OSError:
            return 127
        try:
            return proc.wait()
        except KeyboardInterrupt:
            _cleanup_process_group(proc.pid)
            _cleanup_processes_by_cmdline(cleanup_cmdline_tokens or [])
            raise


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
    _, exit_code = write_reports(run_dir)
    return exit_code


def run_context(root: Path, build_dir: Path, machine: str, out: Path) -> int:
    result = inspect_context(root, build_dir, machine)
    write_json(out, result)
    return 2 if result.get("status") == "blocked" else 0


def run_basic(
    root: Path,
    build_dir: Path,
    machine: str,
    image: str,
    timeout: int,
    out_dir: Path,
    dry_run: bool,
    preflight_only: bool = False,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    commands_file = out_dir / "commands.jsonl"
    preflight_json = out_dir / "preflight.json"
    preflight_stdout = out_dir / "logs/preflight.stdout.log"
    preflight_stderr = out_dir / "logs/preflight.stderr.log"
    preflight_argv = [
        PYTHON,
        "scripts/test/run_test_manifest.py",
        "preflight",
        "--build-dir",
        str(build_dir),
        "--machine",
        machine,
        "--image",
        image,
        "--out",
        str(preflight_json),
    ]
    _log("START runtime-preflight")
    started = now()
    start = monotonic()
    preflight_rc = _run_subprocess(preflight_argv, root, preflight_stdout, preflight_stderr)
    preflight_data: JsonObject = {}
    if preflight_json.is_file():
        preflight_data = __import__("json").loads(preflight_json.read_text(encoding="utf-8"))
    if preflight_rc != 0:
        _record_command(
            commands_file,
            "runtime-preflight",
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
        _log("DONE runtime-preflight (blocked)")
        _write_summary(out_dir)
        return 2
    _record_command(
        commands_file,
        "runtime-preflight",
        preflight_argv,
        "pass",
        started,
        monotonic() - start,
        preflight_rc,
        preflight_stdout,
        preflight_stderr,
        artifacts=[{"kind": "preflight", "path": str(preflight_json)}],
    )
    _log("DONE runtime-preflight (pass)")
    if preflight_only:
        return _write_summary(out_dir)

    boot_out = out_dir / "fvp"
    basic_argv = [
        PYTHON,
        "scripts/run/runfvp_log_boot.py",
        "--machine",
        machine,
        "--timeout",
        str(timeout),
        "--min-runtime",
        str(min(70, timeout)),
        "--out-dir",
        str(boot_out),
        "--require",
        "all",
        "--post-login-command",
        "true",
    ]
    if dry_run:
        _log("SKIP basic-boot (dry-run)")
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
    _log("START basic-boot")
    _log(f"PROGRESS basic-boot: logs under {boot_out}")
    started = now()
    start = monotonic()
    rc = _run_subprocess(
        basic_argv,
        root,
        stdout_log,
        stderr_log,
        [str(boot_out.resolve())],
    )
    result_json = boot_out / "result.json"
    status = _status_from_rc(rc)
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
            {"kind": "fvp_summary", "path": str(boot_out / "summary.txt")},
        ],
    )
    _print_boot_summary(boot_out / "summary.txt")
    _log(f"DONE basic-boot ({status})")
    return _write_summary(out_dir)


def _run_oeqa(
    root: Path,
    build_dir: Path,
    machine: str,
    image: str,
    timeout_oeqa: int,
    out_dir: Path,
    dry_run: bool,
    host_python_bin: Path | None,
    kinds: list[str],
) -> int:
    argv = [
        PYTHON,
        "scripts/test/run_test_oeqa_lanes.py",
        "--run-dir",
        str(out_dir),
        "--commands-file",
        str(out_dir / "commands.jsonl"),
        "--build-dir",
        str(build_dir),
        "--machine",
        machine,
        "--image",
        image,
        "--timeout-oeqa",
        str(timeout_oeqa),
    ]
    if host_python_bin is not None:
        argv.extend(["--host-python-bin", str(host_python_bin)])
    for kind in kinds:
        argv.extend(["--kind", kind])
    if dry_run:
        argv.append("--dry-run")
    _log("START oeqa-lanes")
    _log(f"PROGRESS oeqa-lanes: logs under {out_dir / 'oeqa'}")
    try:
        process = subprocess.Popen(argv, cwd=root, start_new_session=True)
    except OSError:
        return 127
    try:
        rc = process.wait()
    except KeyboardInterrupt:
        _cleanup_process_group(process.pid)
        _stop_bitbake_server(root, build_dir, host_python_bin)
        raise
    finally:
        if process.poll() is None:
            _cleanup_process_group(process.pid)
    status = _status_from_rc(rc)
    _log(f"DONE oeqa-lanes ({status})")
    return rc


def run_functional(
    root: Path,
    build_dir: Path,
    machine: str,
    image: str,
    timeout_fvp: int,
    timeout_oeqa: int,
    out_dir: Path,
    dry_run: bool,
    preflight_only: bool = False,
) -> int:
    preflight_rc = run_basic(
        root,
        build_dir,
        machine,
        image,
        timeout_fvp,
        out_dir,
        dry_run,
        True,
    )
    if preflight_rc != 0 or preflight_only:
        return preflight_rc
    host_python = None if dry_run else _select_host_python(out_dir)
    if host_python is None and not dry_run:
        return _write_summary(out_dir)
    oeqa_rc = _run_oeqa(
        root,
        build_dir,
        machine,
        image,
        timeout_oeqa,
        out_dir,
        dry_run,
        host_python,
        [os.environ.get("APOLLO_VALIDATION_OEQA_KIND", "functional")],
    )
    if oeqa_rc == 70:
        return 70
    _write_summary(out_dir)
    return oeqa_rc


def run_power(
    root: Path,
    build_dir: Path,
    machine: str,
    image: str,
    timeout_oeqa: int,
    out_dir: Path,
    dry_run: bool,
    preflight_only: bool = False,
) -> int:
    preflight_rc = run_basic(
        root,
        build_dir,
        machine,
        image,
        0,
        out_dir,
        dry_run,
        True,
    )
    if preflight_rc != 0 or preflight_only:
        return preflight_rc
    host_python = None if dry_run else _select_host_python(out_dir)
    if host_python is None and not dry_run:
        return _write_summary(out_dir)
    oeqa_rc = _run_oeqa(
        root,
        build_dir,
        machine,
        image,
        timeout_oeqa,
        out_dir,
        dry_run,
        host_python,
        ["power"],
    )
    _write_summary(out_dir)
    return oeqa_rc


def run_category(args: Any) -> int:
    root = args.root.resolve()
    build_dir = args.build_dir
    out_dir = args.out_dir
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    if args.category == "basic":
        return run_basic(
            root,
            build_dir,
            args.machine,
            args.image,
            args.timeout,
            out_dir,
            args.dry_run,
            getattr(args, "preflight_only", False),
        )
    if args.category == "functional":
        return run_functional(
            root,
            build_dir,
            args.machine,
            args.image,
            args.timeout,
            args.timeout_oeqa,
            out_dir,
            args.dry_run,
            getattr(args, "preflight_only", False),
        )
    if args.category == "power":
        return run_power(
            root,
            build_dir,
            args.machine,
            args.image,
            args.timeout_oeqa,
            out_dir,
            args.dry_run,
            getattr(args, "preflight_only", False),
        )
    data = list_suites(category=args.category)
    write_json(out_dir / "suite.json", data)
    commands_file = out_dir / "commands.jsonl"
    _log(f"START {args.category}-list")
    _log(f"PROGRESS {args.category}-list: suite metadata {out_dir / 'suite.json'}")
    _record_command(
        commands_file,
        f"{args.category}-list",
        [PYTHON, "-m", "apollo_validation.cli", "list", "--category", args.category],
        "skipped",
        now(),
        0.0,
        artifacts=[{"kind": "suite", "path": str(out_dir / "suite.json")}],
    )
    _log(f"DONE {args.category}-list (pass)")
    return _write_summary(out_dir)
