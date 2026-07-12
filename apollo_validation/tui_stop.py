from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def _read_ppid(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        return None
    return None


def _read_cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return ""


def _process_tree(root_pid: int) -> list[int]:
    children: dict[int, list[int]] = defaultdict(list)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        parent = _read_ppid(pid)
        if parent is not None:
            children[parent].append(pid)

    ordered: list[int] = []

    def visit(pid: int) -> None:
        for child in children.get(pid, []):
            visit(child)
        ordered.append(pid)

    visit(root_pid)
    return ordered


def _signal_existing(pids: list[int], sig: signal.Signals) -> list[int]:
    signalled: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (PermissionError, ProcessLookupError):
            continue
        signalled.append(pid)
    return signalled


def _existing(pids: list[int]) -> list[int]:
    return [pid for pid in pids if Path(f"/proc/{pid}").exists()]


def _related_runtime_pids(root: Path, build_dir: Path, protected: set[int]) -> list[int]:
    root_text = str(root.resolve())
    build_text = str(build_dir.resolve())
    markers = ("/bitbake/bin/bitbake", "FVP_Zena_CSS_Cfg2")
    related: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in protected:
            continue
        cmdline = _read_cmdline(pid)
        if (
            cmdline
            and (root_text in cmdline or build_text in cmdline)
            and any(marker in cmdline for marker in markers)
        ):
            related.append(pid)
    return related


def _signal_groups(pids: list[int], sig: signal.Signals) -> list[int]:
    groups: set[int] = set()
    for pid in pids:
        try:
            groups.add(os.getpgid(pid))
        except ProcessLookupError:
            continue
    signalled: list[int] = []
    for group in groups:
        try:
            os.killpg(group, sig)
        except (PermissionError, ProcessLookupError):
            continue
        signalled.append(group)
    return signalled


def _ancestors(pid: int) -> set[int]:
    ancestors = {pid}
    while pid > 1:
        parent = _read_ppid(pid)
        if parent is None or parent in ancestors:
            break
        ancestors.add(parent)
        pid = parent
    return ancestors


def _write_status(path: Path, status: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".stop.tmp")
    temporary.write_text(f"{status}\n", encoding="utf-8")
    temporary.replace(path)


def stop_session(args: argparse.Namespace) -> int:
    started = time.monotonic()
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        root_pid = int(args.runner_pid.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        root_pid = 0
    protected = _ancestors(os.getpid())
    pids = [
        pid
        for pid in (_process_tree(root_pid) if root_pid > 0 else [])
        if pid not in protected
    ]
    _write_status(args.status, 130)
    orchestrators = [
        pid for pid in pids if "run_test_oeqa_lanes.py" in _read_cmdline(pid)
    ]
    interrupt_targets = orchestrators or [pid for pid in pids if pid != root_pid]
    interrupted = _signal_existing(interrupt_targets, signal.SIGINT)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        active_children = [pid for pid in _existing(pids) if pid != root_pid]
        if not active_children:
            break
        time.sleep(0.2)
    remaining = _existing(pids)
    terminated = _signal_existing(remaining, signal.SIGTERM)
    time.sleep(1.0)
    remaining = _existing(pids)
    killed = _signal_existing(remaining, signal.SIGKILL)
    time.sleep(0.1)
    remaining = _existing(pids)
    detached = _related_runtime_pids(args.root, args.build_dir, protected)
    detached_deadline = time.monotonic() + 5.0
    while detached and time.monotonic() < detached_deadline:
        time.sleep(0.2)
        detached = _related_runtime_pids(args.root, args.build_dir, protected)
    detached_terminated = _signal_groups(detached, signal.SIGTERM)
    time.sleep(1.0)
    detached = _related_runtime_pids(args.root, args.build_dir, protected)
    detached_killed = _signal_groups(detached, signal.SIGKILL)
    detached_deadline = time.monotonic() + 30.0
    while detached and time.monotonic() < detached_deadline:
        time.sleep(0.2)
        detached = _related_runtime_pids(args.root, args.build_dir, protected)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "root_pid": root_pid,
                "process_tree": pids,
                "interrupted": interrupted,
                "orchestrators": orchestrators,
                "terminated": terminated,
                "killed": killed,
                "detached_terminated_groups": detached_terminated,
                "detached_killed_groups": detached_killed,
                "detached_remaining": detached,
                "remaining": remaining,
                "status": 130,
                "duration_s": round(time.monotonic() - started, 3),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [args.tmux_bin, "wait-for", "-S", args.done_channel],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [args.tmux_bin, "kill-session", "-t", args.session],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tmux-bin", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--done-channel", required=True)
    parser.add_argument("--runner-pid", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    return stop_session(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
