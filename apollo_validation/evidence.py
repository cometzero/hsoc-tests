from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def run_log(message: str) -> None:
    print(f"[{now()}] [run_test] {message}", flush=True)


def write_json(path: Path, data: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_record(path: Path, record: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


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
    records = read_records(run_dir / "commands.jsonl")
    statuses = [str(record.get("status", "")) for record in records]
    blockers: list[JsonObject] = []
    for record in records:
        record_blockers = record.get("blockers", [])
        if isinstance(record_blockers, list):
            blockers.extend(item for item in record_blockers if isinstance(item, dict))
    if any(status == "fail" for status in statuses):
        status = "FAIL"
        exit_code = 1
    elif any(status == "blocked" for status in statuses) or not records:
        status = "BLOCKED"
        exit_code = 2
    else:
        status = "PASS"
        exit_code = 0
    summary: JsonObject = {
        "status": status,
        "exit_code": exit_code,
        "run_dir": str(run_dir),
        "records": records,
        "record_count": len(records),
        "blockers": blockers,
    }
    return summary, exit_code
