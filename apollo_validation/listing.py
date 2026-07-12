from __future__ import annotations

from pathlib import Path

from .evidence import append_record, now, run_log, write_json
from .suites import list_suites


def run_list(run_dir: Path, category: str | None) -> None:
    label = category if category is not None else "all"
    run_log(f"START category-{label}-list")
    suite_path = run_dir / "suite.json"
    data = list_suites(category=category)
    write_json(suite_path, data)
    for name, entries in data.get("categories", {}).items():
        print(f"{name}:")
        for entry in entries:
            print(f"  {entry.get('name')}")
    record_argv = ["apollo_validation.cli", "list"]
    if category is not None:
        record_argv.extend(("--category", category))
    append_record(
        run_dir / "commands.jsonl",
        {
            "name": f"category-{label}-list",
            "argv": record_argv,
            "status": "pass",
            "started_at": now(),
            "finished_at": now(),
            "required": False,
            "artifacts": [{"kind": "suite", "path": str(suite_path)}],
        },
    )
    run_log(f"DONE category-{label}-list (pass)")
