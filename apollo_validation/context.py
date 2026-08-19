from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


JsonObject = dict[str, Any]

CONF_PATTERN = re.compile(
    r"^([A-Za-z0-9_]+)(?::([A-Za-z0-9_:+-]+))?\s*(\?\?=|\?=|\+=|=)\s*\"(.*)\"$"
)


def _logical_conf_lines(path: Path) -> list[str]:
    lines: list[str] = []
    pending = ""
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        lines.append((pending + stripped).strip())
        pending = ""
    if pending:
        lines.append(pending.strip())
    return lines


def parse_conf(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _logical_conf_lines(path):
        match = CONF_PATTERN.match(line)
        if match is None:
            continue
        key, suffix, operator, raw_value = match.groups()
        value = " ".join(raw_value.split())
        if suffix is not None and suffix.endswith("append"):
            values[key] = f"{values.get(key, '')} {value}".strip()
            continue
        if operator == "+=":
            values[key] = f"{values.get(key, '')} {value}".strip()
            continue
        if operator in {"?=", "??="} and key in values:
            continue
        values[key] = value
    return values


def words(value: str) -> list[str]:
    return [part for part in value.split() if part]


def _read_json(path: Path) -> JsonObject:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _str(data: JsonObject, key: str, default: str = "") -> str:
    value = data.get(key, default)
    return value if isinstance(value, str) else default


def _tmpdir_path(build_dir: Path, tmpdir: str) -> Path:
    if tmpdir.startswith("${TOPDIR}/"):
        return build_dir / tmpdir.removeprefix("${TOPDIR}/")
    return build_dir / tmpdir


def inspect_context(
    root: Path,
    build_dir: Path,
    machine: str,
    image: str = "nexios-image",
) -> JsonObject:
    root = root.resolve()
    build_dir = build_dir if build_dir.is_absolute() else root / build_dir
    local_conf = parse_conf(build_dir / "conf/local.conf")
    bblayers_conf = parse_conf(build_dir / "conf/bblayers.conf")
    template_path = build_dir / "conf/templateconf.cfg"
    templateconf = (
        template_path.read_text(encoding="utf-8").strip()
        if template_path.is_file()
        else ""
    )
    tmpdir = local_conf.get("TMPDIR", "${TOPDIR}/tmp_baremetal")
    deploy_dir = _tmpdir_path(build_dir, tmpdir) / "deploy/images" / machine
    testdata_path = deploy_dir / f"{image}-{machine}.testdata.json"
    fvpconf_path = deploy_dir / f"{image}-{machine}.fvpconf"
    testdata = _read_json(testdata_path)
    fvpconf = _read_json(fvpconf_path)
    distro = local_conf.get("DISTRO") or _str(testdata, "DISTRO", "auto-ad-nexios")
    distro_conf = parse_conf(
        root
        / "hsoc-stack/yocto/meta-hsoc-auto-solutions/conf/distro"
        / f"{distro}.conf"
    )
    status = "ok" if testdata_path.is_file() and fvpconf_path.is_file() else "blocked"
    blockers = []
    if not testdata_path.is_file():
        blockers.append(
            {
                "reason": "blocked_missing_artifact",
                "path": str(testdata_path),
                "name": "testdata",
            }
        )
    if not fvpconf_path.is_file():
        blockers.append(
            {
                "reason": "blocked_missing_artifact",
                "path": str(fvpconf_path),
                "name": "fvpconf",
            }
        )
    return {
        "status": status,
        "machine": machine,
        "image": image,
        "distro": distro,
        "rd_aspen_variant": local_conf.get("RD_ASPEN_VARIANT")
        or _str(testdata, "RD_ASPEN_VARIANT", "cfg2"),
        "pc_cpus_count_default": int(
            local_conf.get("PC_CPUS_COUNT_DEFAULT")
            or _str(testdata, "PC_CPUS_COUNT_DEFAULT", "0")
            or 0
        ),
        "si_cl1_cpus_count": int(_str(testdata, "SI_CL1_CPUS_COUNT", "4") or 4),
        "tmpdir": tmpdir,
        "extra_image_features": words(
            f"{local_conf.get('EXTRA_IMAGE_FEATURES', '')} "
            f"{_str(testdata, 'EXTRA_IMAGE_FEATURES')}"
        ),
        "image_classes": words(
            f"{local_conf.get('IMAGE_CLASSES', '')} {_str(testdata, 'IMAGE_CLASSES')}"
        ),
        "test_suites": words(_str(testdata, "TEST_SUITES")),
        "skip_suites": words(
            f"{_str(testdata, 'HSOC_RUN_TEST_SKIP_SUITES')} "
            f"{distro_conf.get('HSOC_RUN_TEST_SKIP_SUITES', '')}"
        ),
        "skip_extra_lanes": words(
            f"{_str(testdata, 'HSOC_RUN_TEST_SKIP_EXTRA_LANES')} "
            f"{distro_conf.get('HSOC_RUN_TEST_SKIP_EXTRA_LANES', '')}"
        ),
        "test_fvp_devices": words(
            distro_conf.get("TEST_FVP_DEVICES", "") or _str(testdata, "TEST_FVP_DEVICES")
        ),
        "test_target": _str(testdata, "TEST_TARGET"),
        "test_target_ip": _str(testdata, "TEST_TARGET_IP"),
        "fvp_exe": _str(testdata, "FVP_EXE") or _str(fvpconf, "exe"),
        "testdata_path": str(testdata_path),
        "fvpconf": {
            "path": str(fvpconf_path),
            "exe": _str(fvpconf, "exe"),
            "provider": _str(fvpconf, "provider"),
            "bindir": _str(fvpconf, "fvp-bindir"),
            "args": fvpconf.get("args", []),
        },
        "bblayers": words(bblayers_conf.get("BBLAYERS", "")),
        "templateconf": templateconf,
        "blockers": blockers,
    }
