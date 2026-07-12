from __future__ import annotations

import json
from importlib import resources
from typing import Any


JsonObject = dict[str, Any]
DEFAULT_PROFILE = "apollo-fvp-cfg2-baremetal-demo"


def _load_profile(profile: str) -> JsonObject:
    if profile != DEFAULT_PROFILE:
        raise ValueError(f"unsupported profile: {profile}")
    data = resources.files(__package__).joinpath(
        "suites/apollo_fvp_cfg2_baremetal_demo.json"
    )
    loaded = json.loads(data.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"{data} must contain an object")
    return loaded


def list_suites(profile: str = DEFAULT_PROFILE, category: str | None = None) -> JsonObject:
    data = _load_profile(profile)
    categories = data.get("categories", {})
    if not isinstance(categories, dict):
        categories = {}
    if category is not None:
        if category not in categories:
            raise ValueError(f"unsupported category: {category}")
        categories = {category: categories[category]}
    return {
        "profile": data.get("profile", profile),
        "machine": data.get("machine", "apollo-fvp"),
        "rd_aspen_variant": data.get("rd_aspen_variant", "cfg2"),
        "pc_cpus_count_default": data.get("pc_cpus_count_default", 4),
        "categories": categories,
        "test_dependencies": data.get("test_dependencies", {}),
        "dependency_categories": data.get("dependency_categories", {}),
        "unselectable": data.get("unselectable", []),
        "excluded": data.get("excluded", []),
    }
