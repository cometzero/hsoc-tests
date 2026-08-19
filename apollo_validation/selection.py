from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path

from .evidence import append_record, now, write_json
from .profiles import load_test_profile
from .root_cli import RootOptions
from .suites import list_suites


SELECTED_SUITES_ENV = "APOLLO_VALIDATION_TEST_SUITES"
SELECTED_KIND_ENV = "APOLLO_VALIDATION_OEQA_KIND"
SELECTED_TARGET_ENV = "APOLLO_VALIDATION_TEST_TARGET"


@dataclass(frozen=True, slots=True)
class SelectionError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class TestNode:
    name: str
    category: str
    dependencies: tuple[str, ...]
    selectable: bool


@dataclass(frozen=True, slots=True)
class TestSelection:
    category: str
    execution_category: str
    oeqa_kind: str | None
    requested: tuple[str, ...]
    ordered_tests: tuple[str, ...]
    profile_name: str | None = None
    profile_path: Path | None = None
    test_target: str | None = None
    backend: str | None = None
    image_profile: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "category": self.category,
            "execution_category": self.execution_category,
            "oeqa_kind": self.oeqa_kind,
            "requested": list(self.requested),
            "ordered_tests": list(self.ordered_tests),
            "profile_name": self.profile_name,
            "profile_path": str(self.profile_path) if self.profile_path else None,
            "test_target": self.test_target,
            "backend": self.backend,
            "image_profile": self.image_profile,
        }


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _nodes() -> dict[str, TestNode]:
    data = list_suites()
    dependencies = data.get("test_dependencies", {})
    dependency_categories = data.get("dependency_categories", {})
    unselectable = set(_string_list(data.get("unselectable", [])))
    if not isinstance(dependencies, dict) or not isinstance(dependency_categories, dict):
        raise SelectionError("invalid test dependency metadata")

    nodes: dict[str, TestNode] = {}
    categories = data.get("categories", {})
    if not isinstance(categories, dict):
        raise SelectionError("invalid test category metadata")
    for category, entries in categories.items():
        if not isinstance(category, str) or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            nodes[name] = TestNode(
                name=name,
                category=category,
                dependencies=_string_list(dependencies.get(name, [])),
                selectable=name not in unselectable,
            )

    for name, category in dependency_categories.items():
        if not isinstance(name, str) or not isinstance(category, str):
            continue
        nodes.setdefault(
            name,
            TestNode(
                name=name,
                category=category,
                dependencies=_string_list(dependencies.get(name, [])),
                selectable=False,
            ),
        )
    return nodes


def _execution_category(category: str) -> tuple[str, str | None]:
    if category == "extended":
        return "functional", "extended"
    if category in {"functional", "power"}:
        return category, category
    if category == "basic":
        return category, None
    raise SelectionError(f"individual tests are not executable for category {category}")


def resolve_selection(test_name: str, explicit_category: str | None) -> TestSelection:
    nodes = _nodes()
    selected = nodes.get(test_name)
    if selected is None:
        raise SelectionError(f"unknown test: {test_name}")
    if not selected.selectable:
        raise SelectionError(f"test is not individually selectable: {test_name}")
    if explicit_category is not None and explicit_category != selected.category:
        raise SelectionError(
            f"test {test_name} belongs to category {selected.category}, not {explicit_category}"
        )

    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise SelectionError(f"dependency cycle detected at test: {name}")
        node = nodes.get(name)
        if node is None:
            raise SelectionError(f"unknown dependency {name} required by {test_name}")
        visiting.add(name)
        for dependency in node.dependencies:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(name)

    visit(test_name)
    execution_category, oeqa_kind = _execution_category(selected.category)
    return TestSelection(
        category=selected.category,
        execution_category=execution_category,
        oeqa_kind=oeqa_kind,
        requested=(test_name,),
        ordered_tests=tuple(ordered),
    )


def prepare_selection(
    root: Path,
    options: RootOptions,
) -> tuple[TestSelection | None, RootOptions]:
    if options.test_name is not None and options.test_profile is not None:
        raise SelectionError("--test cannot be combined with --test-profile")
    if options.test_profile is not None:
        if options.list_suites:
            raise SelectionError("--test-profile cannot be combined with --list")
        profile = load_test_profile(
            root,
            options.test_profile,
            options.backend,
            options.image_profile,
        )
        timeout_oeqa = (
            options.timeout_oeqa
            if options.timeout_oeqa_requested
            else profile.timeout_seconds
        )
        return (
            TestSelection(
                category="profile",
                execution_category="functional",
                oeqa_kind=profile.oeqa_kind,
                requested=(profile.name,),
                ordered_tests=profile.selectors,
                profile_name=profile.name,
                profile_path=profile.path,
                test_target=profile.test_target,
                backend=profile.backend,
                image_profile=profile.image_profile,
            ),
            replace(
                options,
                category="functional",
                timeout_oeqa=timeout_oeqa,
            ),
        )
    if options.test_name is None:
        return None, options
    if options.list_suites:
        raise SelectionError("--test cannot be combined with --list")
    explicit_category = options.category if options.category_requested else None
    selection = resolve_selection(options.test_name, explicit_category)
    return selection, replace(options, category=selection.execution_category)


def write_selection_evidence(run_dir: Path, selection: TestSelection) -> None:
    selection_path = run_dir / "selection.json"
    write_json(selection_path, selection.as_json())
    profile_snapshot: Path | None = None
    if selection.profile_path is not None:
        profile_snapshot = run_dir / "resolved-profile.yaml"
        profile_data = selection.as_json()
        profile_snapshot.write_text(
            json.dumps(profile_data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    artifacts = [{"kind": "selection", "path": str(selection_path)}]
    if profile_snapshot is not None:
        artifacts.append(
            {"kind": "resolved_profile", "path": str(profile_snapshot)}
        )
    append_record(
        run_dir / "commands.jsonl",
        {
            "name": "test-selection",
            "argv": ["apollo_validation.cli", "select", *selection.requested],
            "status": "pass",
            "started_at": now(),
            "finished_at": now(),
            "required": True,
            "artifacts": artifacts,
        },
    )


@contextmanager
def selected_test_environment(selection: TestSelection | None) -> Iterator[None]:
    previous_suites = os.environ.get(SELECTED_SUITES_ENV)
    previous_kind = os.environ.get(SELECTED_KIND_ENV)
    previous_target = os.environ.get(SELECTED_TARGET_ENV)
    try:
        if selection is not None and selection.oeqa_kind is not None:
            os.environ[SELECTED_SUITES_ENV] = json.dumps(selection.ordered_tests)
            os.environ[SELECTED_KIND_ENV] = selection.oeqa_kind
        if selection is not None and selection.test_target is not None:
            os.environ[SELECTED_TARGET_ENV] = selection.test_target
        yield
    finally:
        if previous_suites is None:
            os.environ.pop(SELECTED_SUITES_ENV, None)
        else:
            os.environ[SELECTED_SUITES_ENV] = previous_suites
        if previous_kind is None:
            os.environ.pop(SELECTED_KIND_ENV, None)
        else:
            os.environ[SELECTED_KIND_ENV] = previous_kind
        if previous_target is None:
            os.environ.pop(SELECTED_TARGET_ENV, None)
        else:
            os.environ[SELECTED_TARGET_ENV] = previous_target
