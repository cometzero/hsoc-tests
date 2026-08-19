from __future__ import annotations

from pathlib import Path

import json

from apollo_validation.evidence import append_record, summarize_records, write_reports


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


def test_final_reports_include_human_json_and_junit_results(
    tmp_path: Path,
) -> None:
    # Given: one passing OEQA PFDI result and its command record.
    result_path = tmp_path / "oeqa/pfdi/results/result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "nexios-bsp-initramfs": {
                    "result": {
                        "test_64_bsp_pfdi.PFDIBspTest.test_cli": {
                            "status": "PASSED",
                            "duration": 0.25,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    append_record(
        tmp_path / "commands.jsonl",
        {
            "name": "oeqa-pfdi",
            "status": "pass",
            "duration_s": 1.0,
            "artifacts": [{"kind": "oeqa_result", "path": str(result_path)}],
        },
    )

    # When: the public final-report contract is rendered.
    summary, exit_code = write_reports(tmp_path)

    # Then: human, machine, and CI surfaces agree on the passing test.
    assert exit_code == 0
    assert summary["counts"]["passed"] == 1
    assert (tmp_path / "summary.txt").is_file()
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "junit.xml").is_file()
    assert "RESULT: PASS" in (tmp_path / "summary.txt").read_text(
        encoding="utf-8"
    )
