from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


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
    assert set(data["categories"]) == {"basic", "functional", "extended", "stress"}
