from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .evidence import write_json


JsonObject = dict[str, Any]


def _git_revision(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def capture_run_inputs(
    root: Path,
    run_dir: Path,
    context: JsonObject,
) -> Path:
    context["input_revisions"] = {
        "workspace": _git_revision(root),
        "qa_runner": _git_revision(root / "hsoc-stack/tests"),
        "bsp_layer": _git_revision(root / "hsoc-stack/yocto/meta-hsoc-bsp"),
        "platform_layer": _git_revision(
            root / "hsoc-stack/yocto/meta-hsoc-auto-solutions"
        ),
    }
    evidence_dir = run_dir / "evidence"
    input_paths = {
        "testdata": context.get("testdata_path"),
        "runtime_config": context.get("fvpconf", {}).get("path")
        if isinstance(context.get("fvpconf"), dict)
        else None,
    }
    entries = []
    for kind, raw_path in input_paths.items():
        if not isinstance(raw_path, str) or not raw_path:
            continue
        source = Path(raw_path)
        if not source.is_file():
            continue
        evidence_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".fvpconf" if kind == "runtime_config" else ".testdata.json"
        snapshot = evidence_dir / f"resolved{suffix}"
        shutil.copy2(source, snapshot)
        entries.append(
            {
                "kind": kind,
                "source": str(source),
                "snapshot": str(snapshot),
                "size": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        )
    path = evidence_dir / "input-manifest.json"
    write_json(path, {"inputs": entries})
    return path
