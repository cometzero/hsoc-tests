from __future__ import annotations

from pathlib import Path

from apollo_validation.evidence import append_record, summarize_records


def test_summary_maps_failure_status(tmp_path: Path) -> None:
    commands = tmp_path / "commands.jsonl"
    append_record(commands, {"name": "a", "status": "pass"})
    append_record(commands, {"name": "b", "status": "fail"})

    summary, exit_code = summarize_records(tmp_path)

    assert summary["status"] == "FAIL"
    assert exit_code == 1


def test_summary_maps_empty_run_to_blocked(tmp_path: Path) -> None:
    summary, exit_code = summarize_records(tmp_path)

    assert summary["status"] == "BLOCKED"
    assert exit_code == 2
