from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from apollo_validation.root_cli import parse_root_args


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "apollo_validation.cli", *args],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_cli_help() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "Apollo FVP validation runner" in result.stdout


def test_cli_list_json() -> None:
    result = run_cli("list", "--format", "json")

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(data["categories"]) == {"basic", "functional", "power", "extended", "stress"}


def test_root_cli_resolves_fvp_bsp_profile() -> None:
    # Given: the public FVP BSP PFDI invocation.
    argv = ["--fvp", "--bsp", "--headless", "--test-profile", "pfdi"]

    # When: the root CLI parses the shorthand options.
    options = parse_root_args(argv)

    # Then: the explicit backend, image, and UI contracts are preserved.
    assert options.backend == "fvp"
    assert options.machine == "apollo-fvp"
    assert options.image == "nexios-bsp-initramfs"
    assert options.image_profile == "bsp"
    assert options.test_profile == "pfdi"
    assert options.tui is False


def test_root_cli_rejects_headless_tui_conflict() -> None:
    # Given: two mutually exclusive presentation modes.
    argv = ["--fvp", "--headless", "--tui"]

    # When/Then: parsing fails instead of choosing one silently.
    with pytest.raises(SystemExit):
        parse_root_args(argv)
