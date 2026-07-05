from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def run_root_compat(root: Path, argv: list[str]) -> int:
    legacy = root / "scripts/test/run_test_legacy.sh"
    if not legacy.is_file():
        print(f"legacy runner not found: {legacy}", file=sys.stderr)
        return 70
    return subprocess.call([str(legacy), *argv], cwd=root)
