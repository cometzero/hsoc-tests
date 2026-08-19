from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def run_log(message: str) -> None:
    line = f"[{now()}] [run_test] {message}"
    print(line, flush=True)
    raw_path = os.environ.get("APOLLO_RUN_TEST_LOG")
    if raw_path:
        path = Path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{line}\n")


def write_json(path: Path, data: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_record(path: Path, record: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    if path.name == "commands.jsonl":
        event = {
            "event": "step_finished",
            "name": record.get("name", "unknown"),
            "status": str(record.get("status", "blocked")).upper(),
            "timestamp": record.get("finished_at", now()),
        }
        with (path.parent / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
        write_json(
            path.parent / "status.json",
            {
                "state": "running",
                "last_event": event,
                "updated_at": now(),
            },
        )


def read_records(path: Path) -> list[JsonObject]:
    if not path.is_file():
        return []
    records: list[JsonObject] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            records.append(data)
    return records


def summarize_records(run_dir: Path) -> tuple[JsonObject, int]:
    from .reporting import summarize_records as summarize

    return summarize(run_dir)


def write_reports(run_dir: Path) -> tuple[JsonObject, int]:
    from .reporting import write_reports as write

    return write(run_dir)
