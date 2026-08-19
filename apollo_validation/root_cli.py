from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Final


CATEGORIES: Final = ("basic", "functional", "power", "extended", "stress")


@dataclass(frozen=True, slots=True)
class RootOptions:
    build_dir: Path
    backend: str
    machine: str
    image: str
    image_profile: str
    out_dir: Path | None
    stamp: str | None
    list_suites: bool
    dry_run: bool
    preflight_only: bool
    skip_runtime: bool
    category: str
    category_requested: bool
    test_name: str | None
    test_profile: str | None
    tui: bool
    headless: bool
    include_qbox_runtime: bool
    timeout_oeqa: int
    timeout_oeqa_requested: bool
    timeout_fvp: int


def _category_was_requested(argv: list[str]) -> bool:
    return any(arg == "--category" or arg.startswith("--category=") for arg in argv)


def _option_was_requested(argv: list[str], name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in argv)


def parse_root_args(argv: list[str]) -> RootOptions:
    parser = argparse.ArgumentParser(description="Apollo validation runner")
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--machine", default="apollo-fvp")
    parser.add_argument("--image", default="nexios-image")
    backend_group = parser.add_mutually_exclusive_group()
    backend_group.add_argument("--fvp", dest="backend", action="store_const", const="fvp")
    backend_group.add_argument("--qbox", dest="backend", action="store_const", const="qbox")
    parser.add_argument("--bsp", action="store_true")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--stamp")
    parser.add_argument("--list", dest="list_suites", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument("--category", choices=CATEGORIES, default="basic")
    parser.add_argument("--test", dest="test_name")
    parser.add_argument("--test-profile")
    ui_group = parser.add_mutually_exclusive_group()
    ui_group.add_argument("--headless", action="store_true")
    ui_group.add_argument("--tui", action="store_true")
    parser.add_argument("--include-qbox-runtime", action="store_true")
    parser.add_argument("--timeout-oeqa", type=int, default=10800)
    parser.add_argument("--timeout-fvp", type=int, default=300)
    parsed = parser.parse_args(argv)
    backend = parsed.backend
    if backend is None:
        backend = "qbox" if parsed.machine == "apollo-qvp" else "fvp"
    machine = "apollo-qvp" if backend == "qbox" else "apollo-fvp"
    if _option_was_requested(argv, "--machine") and parsed.machine != machine:
        parser.error(f"--{backend} conflicts with --machine {parsed.machine}")
    image = "nexios-bsp-initramfs" if parsed.bsp else parsed.image
    if parsed.bsp and _option_was_requested(argv, "--image") and (
        parsed.image != "nexios-bsp-initramfs"
    ):
        parser.error(f"--bsp conflicts with --image {parsed.image}")
    image_profile = "bsp" if image == "nexios-bsp-initramfs" else "product"
    return RootOptions(
        build_dir=parsed.build_dir,
        backend=backend,
        machine=machine,
        image=image,
        image_profile=image_profile,
        out_dir=parsed.out_dir,
        stamp=parsed.stamp,
        list_suites=parsed.list_suites,
        dry_run=parsed.dry_run,
        preflight_only=parsed.preflight_only,
        skip_runtime=parsed.skip_runtime,
        category=parsed.category,
        category_requested=_category_was_requested(argv),
        test_name=parsed.test_name,
        test_profile=parsed.test_profile,
        tui=parsed.tui,
        headless=not parsed.tui,
        include_qbox_runtime=parsed.include_qbox_runtime,
        timeout_oeqa=parsed.timeout_oeqa,
        timeout_oeqa_requested=_option_was_requested(argv, "--timeout-oeqa"),
        timeout_fvp=parsed.timeout_fvp,
    )


def print_help(root: Path) -> int:
    help_script = root / "scripts/test/run_test_cli.py"
    if help_script.is_file():
        namespace: dict[str, str] = {"__name__": "__run_test_cli__"}
        exec(help_script.read_text(encoding="utf-8"), namespace)
        print(namespace["USAGE"])
        return 0
    print("Usage: ./run_test.sh [options]")
    print("  --category basic|functional|power|extended|stress")
    print("  --test TEST")
    print("  --tui")
    return 0
