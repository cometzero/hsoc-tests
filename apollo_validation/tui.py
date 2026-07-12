from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from .evidence import run_log
from .tui_target import PLATFORM_LOG_PANE, TARGET_LOG_PANES, target_log_command


@dataclass(frozen=True, slots=True)
class TuiArtifacts:
    directory: Path
    command: Path
    console: Path
    status: Path
    layout: Path
    runner_pid: Path
    stop: Path


@dataclass(frozen=True, slots=True)
class TuiRun:
    tmux_bin: str
    start_channel: str
    done_channel: str
    child_argv: tuple[str, ...]
    artifacts: TuiArtifacts


def _tmux_session(run_dir: Path) -> str | None:
    default = re.sub(r"[^A-Za-z0-9_-]+", "-", f"run-test-{run_dir.name}")
    session = os.environ.get("RUN_TEST_TMUX_SESSION", default[:96])
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        return None
    return session


def _child_argv(root: Path, argv: list[str], run_dir: Path) -> tuple[str, ...]:
    filtered: list[str] = []
    skip_value = False
    for arg in argv:
        if skip_value:
            skip_value = False
            continue
        if arg == "--tui":
            continue
        if arg == "--out-dir":
            skip_value = True
            continue
        if arg.startswith("--out-dir="):
            continue
        filtered.append(arg)
    try:
        out_dir = run_dir.relative_to(root)
    except ValueError:
        out_dir = run_dir
    return (str(root / "run_test.sh"), *filtered, "--out-dir", str(out_dir))


def _build_dir(root: Path, argv: list[str]) -> Path:
    for index, arg in enumerate(argv):
        if arg == "--build-dir" and index + 1 < len(argv):
            value = Path(argv[index + 1])
            return value if value.is_absolute() else root / value
        if arg.startswith("--build-dir="):
            value = Path(arg.split("=", 1)[1])
            return value if value.is_absolute() else root / value
    return root / "build"


def _artifacts(run_dir: Path) -> TuiArtifacts:
    directory = run_dir / "tui"
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = TuiArtifacts(
        directory=directory,
        command=directory / "command.txt",
        console=directory / "console.log",
        status=directory / "status",
        layout=directory / "layout.json",
        runner_pid=directory / "runner.pid",
        stop=directory / "stop.json",
    )
    artifacts.console.write_text("", encoding="utf-8")
    artifacts.status.unlink(missing_ok=True)
    artifacts.runner_pid.unlink(missing_ok=True)
    artifacts.stop.unlink(missing_ok=True)
    return artifacts


def _runner_body(run: TuiRun) -> str:
    command = shlex.join(run.child_argv)
    status_tmp = run.artifacts.status.with_suffix(".tmp")
    wait_start = shlex.join((run.tmux_bin, "wait-for", run.start_channel))
    signal_done = shlex.join((run.tmux_bin, "wait-for", "-S", run.done_channel))
    return " ".join(
        (
            "status=70;",
            "finish() {",
            f"if [[ ! -e {shlex.quote(str(run.artifacts.status))} ]]; then",
            f"printf '%s\\n' \"$status\" > {shlex.quote(str(status_tmp))};",
            f"mv {shlex.quote(str(status_tmp))} {shlex.quote(str(run.artifacts.status))};",
            f"{signal_done};",
            "fi;",
            "};",
            "trap finish EXIT INT TERM;",
            f"printf '%s\\n' \"$$\" > {shlex.quote(str(run.artifacts.runner_pid))};",
            f"{wait_start};",
            f"printf 'Command:\\n  %s\\n\\n' {shlex.quote(command)};",
            "printf 'Status: running\\n';",
            f"{command} 2>&1 | tee {shlex.quote(str(run.artifacts.console))};",
            "status=${PIPESTATUS[0]};",
            "trap - EXIT INT TERM;",
            "finish;",
            "printf 'Status: exited %s\\n' \"$status\";",
            "exec tail -f /dev/null",
        )
    )


def _tmux_run(tmux_bin: str, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [tmux_bin, *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
    )


def _read_status(status_path: Path) -> int:
    try:
        status = int(status_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 70
    return status if 0 <= status <= 255 else 70


def _print_final_result(console_path: Path) -> None:
    try:
        lines = console_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for prefix in ("RESULT: ", "SUMMARY: "):
        line = next((line for line in reversed(lines) if line.startswith(prefix)), None)
        if line is not None:
            print(line, flush=True)


def run_tui(root: Path, argv: list[str], run_dir: Path) -> int:
    tmux_name = os.environ.get("TMUX_BIN", "tmux")
    tmux_bin = shutil.which(tmux_name)
    if tmux_bin is None:
        print(f"error: tmux executable not found: {tmux_name}", file=sys.stderr)
        return 70
    session = _tmux_session(run_dir)
    if session is None:
        print("error: RUN_TEST_TMUX_SESSION contains invalid characters", file=sys.stderr)
        return 64
    if _tmux_run(tmux_bin, "has-session", "-t", session).returncode == 0:
        print(f"error: tmux session already exists: {session}", file=sys.stderr)
        return 64

    artifacts = _artifacts(run_dir)
    child_argv = _child_argv(root, argv, run_dir)
    command = shlex.join(child_argv)
    artifacts.command.write_text(command + "\n", encoding="utf-8")
    start_channel = f"{session}-start"
    done_channel = f"{session}-done"
    key_table = f"{session}-keys"
    window = f"{session}:tests"
    runner_body = _runner_body(
        TuiRun(tmux_bin, start_channel, done_channel, child_argv, artifacts)
    )
    created = False
    waiter: subprocess.Popen[bytes] | None = None
    attached: subprocess.Popen[bytes] | None = None
    try:
        new_session = _tmux_run(
            tmux_bin,
            "new-session",
            "-d",
            "-x",
            "160",
            "-y",
            "48",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            session,
            "-n",
            "tests",
            "bash",
            "-lc",
            shlex.join(
                target_log_command(
                    sys.executable,
                    run_dir.resolve(),
                    PLATFORM_LOG_PANE,
                )
            ),
            capture=True,
        )
        if new_session.returncode != 0:
            print(f"error: unable to create tmux session: {new_session.stderr.strip()}", file=sys.stderr)
            return 70
        created = True
        platform_pane = new_session.stdout.strip()
        split_target = platform_pane
        target_panes: list[dict[str, str | list[str]]] = []
        for pane in TARGET_LOG_PANES:
            split_options = [pane.split_direction]
            if pane.split_before:
                split_options.append("-b")
            split = _tmux_run(
                tmux_bin,
                "split-window",
                *split_options,
                "-l",
                pane.split_size,
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                split_target,
                "bash",
                "-lc",
                shlex.join(target_log_command(sys.executable, run_dir.resolve(), pane)),
                capture=True,
            )
            if split.returncode != 0:
                print(
                    f"error: unable to create FVP target pane {pane.domain}: "
                    f"{split.stderr.strip()}",
                    file=sys.stderr,
                )
                return 70
            split_target = split.stdout.strip()
            target_panes.append(
                {"id": split_target, "title": pane.domain, "patterns": list(pane.patterns)}
            )
        test_split = _tmux_run(
            tmux_bin,
            "split-window",
            "-h",
            "-l",
            "50%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            platform_pane,
            "bash",
            "-lc",
            runner_body,
            capture=True,
        )
        if test_split.returncode != 0:
            print(
                "error: unable to create test runner pane: "
                f"{test_split.stderr.strip()}",
                file=sys.stderr,
            )
            return 70
        test_pane = test_split.stdout.strip()
        _tmux_run(tmux_bin, "set-option", "-t", session, "mouse", "on")
        _tmux_run(tmux_bin, "set-window-option", "-t", window, "pane-border-status", "top")
        _tmux_run(
            tmux_bin,
            "set-window-option",
            "-t",
            window,
            "pane-border-format",
            "#{pane_index}: #{pane_title}",
        )
        _tmux_run(tmux_bin, "select-pane", "-t", platform_pane, "-T", "platform")
        for target_pane in target_panes:
            _tmux_run(
                tmux_bin,
                "select-pane",
                "-t",
                str(target_pane["id"]),
                "-T",
                str(target_pane["title"]),
            )
        _tmux_run(tmux_bin, "select-pane", "-t", test_pane, "-T", "tests")
        stop_argv = (
                sys.executable,
                str(Path(__file__).with_name("tui_stop.py")),
                "--tmux-bin",
                tmux_bin,
                "--session",
                session,
                "--root",
                str(root),
                "--build-dir",
                str(_build_dir(root, argv).resolve()),
                "--done-channel",
                done_channel,
                "--runner-pid",
                str(artifacts.runner_pid),
                "--status",
                str(artifacts.status),
                "--evidence",
                str(artifacts.stop),
            )
        stop_log = artifacts.directory / "stop.log"
        stop_command = (
            f"{shlex.join(stop_argv)} >> {shlex.quote(str(stop_log))} 2>&1"
        )
        _tmux_run(
            tmux_bin,
            "bind-key",
            "-T",
            key_table,
            "F12",
            "run-shell",
            "-b",
            stop_command,
        )
        _tmux_run(tmux_bin, "set-option", "-t", session, "key-table", key_table)
        artifacts.layout.write_text(
            json.dumps(
                {
                    "session": session,
                    "window": "tests",
                    "keys": {"F12": "stop test session"},
                    "panes": [
                        {
                            "id": platform_pane,
                            "title": "platform",
                            "patterns": list(PLATFORM_LOG_PANE.patterns),
                        },
                        *target_panes,
                        {"id": test_pane, "title": "tests"},
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        run_log(f"TUI session: {session}")
        run_log(f"TUI command: {artifacts.command}")
        run_log(f"TUI test log: {artifacts.console}")
        run_log("TUI key: F12 stops the test session")
        run_log(
            f"TUI target logs: {run_dir / 'fvp'}, "
            f"{run_dir / 'qvp'}, {run_dir / 'oeqa'}"
        )

        active_waiter = subprocess.Popen([tmux_bin, "wait-for", done_channel])
        waiter = active_waiter
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        if interactive and os.environ.get("TMUX"):
            _tmux_run(tmux_bin, "switch-client", "-t", window)
        elif interactive:
            attached = subprocess.Popen([tmux_bin, "attach-session", "-t", session])
        else:
            run_log("TUI is detached because no terminal is attached")
        _tmux_run(tmux_bin, "wait-for", "-S", start_channel)
        waiter_rc = active_waiter.wait()
        if waiter_rc != 0:
            return 70
        status = _read_status(artifacts.status)
        _print_final_result(artifacts.console)
        return status
    except KeyboardInterrupt:
        return 130
    finally:
        _tmux_run(tmux_bin, "unbind-key", "-T", f"{session}-keys", "F12")
        if created:
            _tmux_run(tmux_bin, "kill-session", "-t", session)
        if waiter is not None and waiter.poll() is None:
            waiter.terminate()
            waiter.wait()
        if attached is not None:
            attached.wait()
