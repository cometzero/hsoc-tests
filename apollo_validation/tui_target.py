from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Final, TextIO


@dataclass(frozen=True, slots=True)
class TargetLogPane:
    domain: str
    title: str
    patterns: tuple[str, ...]
    split_direction: str
    split_size: str
    split_before: bool = False


PLATFORM_LOG_PANE: Final = TargetLogPane(
    "platform",
    "FVP / QBox platform",
    (
        "fvp/fvp_stdout.log",
        "qvp/qbox-platform.log",
        "oeqa/*/logs/fvp_log*",
    ),
    "",
    "",
)

TARGET_LOG_PANES: Final = (
    TargetLogPane(
        "u_boot_linux",
        "U-Boot / Linux",
        (
            "fvp/terminal_ns_uart0_[0-9]*.log",
            "qvp/qbox-primary-console.log",
            "oeqa/*/logs/default_log*",
        ),
        "-v",
        "70%",
        True,
    ),
    TargetLogPane(
        "rse",
        "RSE / TF-M",
        (
            "fvp/terminal_uart_[0-9]*.log",
            "qvp/qbox-rse.log",
            "oeqa/*/logs/rse_log*",
        ),
        "-h",
        "40%",
    ),
    TargetLogPane(
        "safety_island_cl0",
        "Safety Island CL0 / SCP-firmware",
        (
            "fvp/terminal_uart_si_cluster0_[0-9]*.log",
            "qvp/qbox-safety-island-cl0.log",
            "oeqa/*/logs/scp_log*",
        ),
        "-v",
        "75%",
    ),
    TargetLogPane(
        "safety_island_cl1",
        "Safety Island CL1 / Zephyr",
        (
            "fvp/terminal_uart_si_cluster1_[0-9]*.log",
            "qvp/qbox-safety-island-cl1.log",
            "oeqa/*/logs/safety_island_c1_log*",
        ),
        "-v",
        "67%",
    ),
    TargetLogPane(
        "tf_a",
        "TF-A / secure-world AP",
        (
            "fvp/terminal_sec_uart_[0-9]*.log",
            "qvp/qbox-secure-console.log",
            "oeqa/*/logs/tf-a_log*",
        ),
        "-v",
        "50%",
    ),
)


def target_log_command(python: str, run_dir: Path, pane: TargetLogPane) -> tuple[str, ...]:
    return (python, str(Path(__file__).resolve()), str(run_dir), pane.domain)


def _target_pane(domain: str) -> TargetLogPane | None:
    return next(
        (
            pane
            for pane in (PLATFORM_LOG_PANE, *TARGET_LOG_PANES)
            if pane.domain == domain
        ),
        None,
    )


def _target_paths(run_dir: Path, pane: TargetLogPane) -> tuple[Path, ...]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in pane.patterns:
        for path in sorted(run_dir.glob(pattern)):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(resolved)
    return tuple(paths)


def _drain(stream: TextIO, path: Path) -> str:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        size = stream.tell()
    if size < stream.tell():
        stream.seek(0)
    return stream.read()


def follow_target_logs(run_dir: Path, pane: TargetLogPane) -> int:
    status_path = run_dir / "tui/status"
    print(f"Target: {pane.title}", flush=True)
    print("Waiting for target log...", flush=True)
    with ExitStack() as stack:
        streams: dict[Path, TextIO] = {}
        while True:
            for path in _target_paths(run_dir, pane):
                if path in streams:
                    continue
                streams[path] = stack.enter_context(
                    path.open("r", encoding="utf-8", errors="replace")
                )
                print(f"\n=== {path.relative_to(run_dir)} ===", flush=True)
            for path, stream in streams.items():
                text = _drain(stream, path)
                if text:
                    print(text, end="", flush=True)
            if status_path.is_file():
                return 0
            time.sleep(0.2)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: tui_target.py RUN_DIR DOMAIN", file=sys.stderr)
        return 64
    pane = _target_pane(args[1])
    if pane is None:
        print(f"error: unknown target log domain: {args[1]}", file=sys.stderr)
        return 64
    return follow_target_logs(Path(args[0]).resolve(), pane)


if __name__ == "__main__":
    raise SystemExit(main())
