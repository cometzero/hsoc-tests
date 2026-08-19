from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .evidence import now, read_records, write_json


JsonObject = dict[str, Any]


def _read_json(path: Path) -> JsonObject:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _normalized_test_status(value: str) -> str:
    normalized = value.upper()
    if normalized in {"PASS", "PASSED", "OK"}:
        return "PASS"
    if normalized in {"FAIL", "FAILED", "ERROR"}:
        return "FAIL"
    if normalized.startswith("SKIP"):
        return "SKIPPED"
    return "BLOCKED"


def _oeqa_tests(run_dir: Path, records: list[JsonObject]) -> list[JsonObject]:
    tests: list[JsonObject] = []
    for record in records:
        artifacts = record.get("artifacts", [])
        if not isinstance(artifacts, list):
            continue
        for artifact in artifacts:
            if not isinstance(artifact, dict) or artifact.get("kind") != "oeqa_result":
                continue
            raw_path = artifact.get("path")
            if not isinstance(raw_path, str):
                continue
            path = Path(raw_path)
            data = _read_json(path if path.is_absolute() else run_dir / path)
            for result_set in data.values():
                if not isinstance(result_set, dict):
                    continue
                results = result_set.get("result", {})
                if not isinstance(results, dict):
                    continue
                for name, result in results.items():
                    if not isinstance(name, str) or not isinstance(result, dict):
                        continue
                    raw_status = result.get("status", "")
                    duration = result.get("duration", result.get("duration_s", 0.0))
                    tests.append(
                        {
                            "name": name,
                            "status": _normalized_test_status(
                                raw_status if isinstance(raw_status, str) else ""
                            ),
                            "duration_s": round(float(duration), 6)
                            if isinstance(duration, int | float)
                            else 0.0,
                            "evidence": raw_path,
                        }
                    )
    return tests


def summarize_records(run_dir: Path) -> tuple[JsonObject, int]:
    records = read_records(run_dir / "commands.jsonl")
    statuses = [str(record.get("status", "")) for record in records]
    blockers = [
        item
        for record in records
        for item in (
            record.get("blockers", [])
            if isinstance(record.get("blockers", []), list)
            else []
        )
        if isinstance(item, dict)
    ]
    tests = _oeqa_tests(run_dir, records)
    if any(status == "fail" for status in statuses) or any(
        test["status"] == "FAIL" for test in tests
    ):
        status, exit_code = "FAIL", 1
    elif any(status == "blocked" for status in statuses) or not records:
        status, exit_code = "BLOCKED", 2
    else:
        status, exit_code = "PASS", 0
    counts = {
        "passed": sum(test["status"] == "PASS" for test in tests),
        "failed": sum(test["status"] == "FAIL" for test in tests),
        "blocked": sum(test["status"] == "BLOCKED" for test in tests),
        "skipped": sum(test["status"] == "SKIPPED" for test in tests),
        "total": len(tests),
    }
    manifest = _read_json(run_dir / "manifest.json")
    selection = _read_json(run_dir / "selection.json")
    artifacts = [
        artifact
        for record in records
        for artifact in (
            record.get("artifacts", [])
            if isinstance(record.get("artifacts", []), list)
            else []
        )
        if isinstance(artifact, dict)
    ]
    failures = [
        test["name"]
        for test in tests
        if test["status"] == "FAIL" and isinstance(test.get("name"), str)
    ]
    summary: JsonObject = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "status": status,
        "exit_code": exit_code,
        "run_dir": str(run_dir),
        "backend": manifest.get("backend"),
        "machine": manifest.get("machine"),
        "image": manifest.get("image"),
        "image_profile": manifest.get("image_profile"),
        "test_profile": selection.get("profile_name")
        or manifest.get("test_profile"),
        "duration_s": round(
            sum(
                float(record.get("duration_s", 0.0))
                for record in records
                if isinstance(record.get("duration_s", 0.0), int | float)
            ),
            6,
        ),
        "counts": counts,
        "tests": tests,
        "failures": failures,
        "warnings": [],
        "artifacts": artifacts,
        "input_revisions": manifest.get("input_revisions", {}),
        "records": records,
        "record_count": len(records),
        "blockers": blockers,
    }
    return summary, exit_code


def _summary_text(summary: JsonObject, run_dir: Path) -> str:
    counts = summary.get("counts", {})
    counts = counts if isinstance(counts, dict) else {}
    lines = [
        f"RESULT: {summary['status']}",
        f"RUN: {summary['run_id']}",
        "TARGET: "
        f"backend={summary.get('backend') or 'unknown'} "
        f"machine={summary.get('machine') or 'unknown'} "
        f"image={summary.get('image_profile') or summary.get('image') or 'unknown'} "
        f"profile={summary.get('test_profile') or 'none'}",
        "TESTS: "
        f"{counts.get('passed', 0)} passed, "
        f"{counts.get('failed', 0)} failed, "
        f"{counts.get('blocked', 0)} blocked, "
        f"{counts.get('skipped', 0)} skipped",
        f"DURATION: {summary.get('duration_s', 0.0)}s",
    ]
    tests = summary.get("tests", [])
    if isinstance(tests, list):
        for test in tests:
            if isinstance(test, dict):
                lines.append(
                    f"[{test.get('status', 'BLOCKED')}] "
                    f"{test.get('name', 'unknown')} "
                    f"{test.get('duration_s', 0.0)}s"
                )
    profile_name = summary.get("test_profile")
    profile_result = _read_json(run_dir / "results" / f"{profile_name}.json")
    cpus = profile_result.get("cpus", [])
    if isinstance(cpus, list) and cpus:
        if profile_name == "pfdi-si-cl1":
            keys = (
                "status_seen",
                "run_success_seen",
                "success_result_seen",
                "force_error_seen",
                "failed_result_seen",
            )
            header = "CPU STATUS RUN SUCCESS_RESULT FORCE_ERROR FAILED_RESULT"
        else:
            keys = ("oor", "online", "monitor_started", "force_error", "sbistc")
            header = "CPU OOR ONLINE MONITOR FORCE_ERROR SBISTC"
        lines.extend(["", "PFDI CPU RESULTS:", header])
        for cpu in cpus:
            if isinstance(cpu, dict):
                lines.append(
                    " ".join(
                        [
                            str(cpu.get("cpu", "?")),
                            *("PASS" if cpu.get(key) is True else "FAIL" for key in keys),
                        ]
                    )
                )
    return "\n".join(lines) + "\n"


def _write_junit(path: Path, summary: JsonObject) -> None:
    counts = summary.get("counts", {})
    counts = counts if isinstance(counts, dict) else {}
    suite = ET.Element(
        "testsuite",
        name=str(summary.get("test_profile") or "apollo-validation"),
        tests=str(counts.get("total", 0)),
        failures=str(counts.get("failed", 0)),
        errors=str(counts.get("blocked", 0)),
        skipped=str(counts.get("skipped", 0)),
        time=str(summary.get("duration_s", 0.0)),
    )
    tests = summary.get("tests", [])
    if isinstance(tests, list):
        for test in tests:
            if not isinstance(test, dict):
                continue
            case = ET.SubElement(
                suite,
                "testcase",
                name=str(test.get("name", "unknown")),
                time=str(test.get("duration_s", 0.0)),
            )
            match test.get("status"):
                case "FAIL":
                    ET.SubElement(case, "failure", message="OEQA test failed")
                case "BLOCKED":
                    ET.SubElement(case, "error", message="OEQA test blocked")
                case "SKIPPED":
                    ET.SubElement(case, "skipped")
                case _:
                    pass
    ET.indent(suite)
    ET.ElementTree(suite).write(path, encoding="unicode", xml_declaration=True)


def write_reports(run_dir: Path) -> tuple[JsonObject, int]:
    summary, exit_code = summarize_records(run_dir)
    write_json(run_dir / "summary.json", summary)
    (run_dir / "summary.txt").write_text(
        _summary_text(summary, run_dir),
        encoding="utf-8",
    )
    _write_junit(run_dir / "junit.xml", summary)
    write_json(
        run_dir / "status.json",
        {"state": "finished", "result": summary["status"], "updated_at": now()},
    )
    return summary, exit_code
