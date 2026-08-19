from __future__ import annotations

import json
from pathlib import Path

from apollo_validation.context import inspect_context


def test_context_reports_apollo_cfg2_from_conf(tmp_path: Path) -> None:
    root = tmp_path
    conf = root / "build/conf"
    conf.mkdir(parents=True)
    (conf / "local.conf").write_text(
        '\n'.join(
            [
                'RD_ASPEN_VARIANT = "cfg2"',
                'MACHINE = "apollo-fvp"',
                'PC_CPUS_COUNT_DEFAULT = "4"',
                'TMPDIR = "${TOPDIR}/tmp_baremetal"',
                'EXTRA_IMAGE_FEATURES:append = " baremetal"',
                'EXTRA_IMAGE_FEATURES:append = " demos"',
                'IMAGE_CLASSES += "testimage"',
                'DISTRO ??= "auto-ad-nexios"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (conf / "bblayers.conf").write_text('BBLAYERS ?= " layer-a layer-b "\n', encoding="utf-8")
    (conf / "templateconf.cfg").write_text("template\n", encoding="utf-8")
    distro = root / "hsoc-stack/yocto/meta-hsoc-auto-solutions/conf/distro"
    distro.mkdir(parents=True)
    (distro / "auto-ad-nexios.conf").write_text(
        'TEST_FVP_DEVICES:apollo-fvp:auto-ad-nexios = "rtc watchdog networking virtiorng"\n',
        encoding="utf-8",
    )
    deploy = root / "build/tmp_baremetal/deploy/images/apollo-fvp"
    deploy.mkdir(parents=True)
    (deploy / "nexios-image-apollo-fvp.testdata.json").write_text(
        json.dumps(
            {
                "TEST_SUITES": "ping ssh",
                "TEST_TARGET": "HSOCOEFVPTarget",
                "TEST_TARGET_IP": "127.0.0.1:2222",
            }
        ),
        encoding="utf-8",
    )
    (deploy / "nexios-image-apollo-fvp.fvpconf").write_text(
        json.dumps({"exe": "FVP_Zena_CSS_Cfg2"}),
        encoding="utf-8",
    )

    result = inspect_context(root, Path("build"), "apollo-fvp")

    assert result["status"] == "ok"
    assert result["machine"] == "apollo-fvp"
    assert result["rd_aspen_variant"] == "cfg2"
    assert result["pc_cpus_count_default"] == 4
    assert {"baremetal", "demos"}.issubset(result["extra_image_features"])
    assert result["fvp_exe"] == "FVP_Zena_CSS_Cfg2"


def test_context_blocks_when_artifacts_are_missing(tmp_path: Path) -> None:
    (tmp_path / "build/conf").mkdir(parents=True)
    (tmp_path / "build/conf/local.conf").write_text('MACHINE = "apollo-fvp"\n', encoding="utf-8")

    result = inspect_context(tmp_path, Path("build"), "apollo-fvp")

    assert result["status"] == "blocked"
    assert {blocker["name"] for blocker in result["blockers"]} == {"testdata", "fvpconf"}


def test_context_selects_bsp_image_artifacts(tmp_path: Path) -> None:
    # Given: a BSP-only deploy containing no product-image artifacts.
    conf = tmp_path / "build/conf"
    conf.mkdir(parents=True)
    (conf / "local.conf").write_text(
        '\n'.join(
            [
                'MACHINE = "apollo-fvp"',
                'TMPDIR = "${TOPDIR}/tmp_baremetal"',
                'DISTRO = "auto-ad-nexios"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (conf / "bblayers.conf").write_text("", encoding="utf-8")
    (conf / "templateconf.cfg").write_text("template\n", encoding="utf-8")
    distro = tmp_path / "hsoc-stack/yocto/meta-hsoc-auto-solutions/conf/distro"
    distro.mkdir(parents=True)
    (distro / "auto-ad-nexios.conf").write_text("", encoding="utf-8")
    deploy = tmp_path / "build/tmp_baremetal/deploy/images/apollo-fvp"
    deploy.mkdir(parents=True)
    stem = deploy / "nexios-bsp-initramfs-apollo-fvp"
    stem.with_suffix(".testdata.json").write_text(
        json.dumps({"TEST_TARGET": "HSOCBSPFVPTarget"}),
        encoding="utf-8",
    )
    stem.with_suffix(".fvpconf").write_text(
        json.dumps({"exe": "FVP_Zena_CSS_Cfg2"}),
        encoding="utf-8",
    )

    # When: context inspection is scoped to the BSP image.
    result = inspect_context(
        tmp_path,
        Path("build"),
        "apollo-fvp",
        "nexios-bsp-initramfs",
    )

    # Then: it resolves only the BSP testdata and FVP configuration.
    assert result["status"] == "ok"
    assert result["image"] == "nexios-bsp-initramfs"
    assert result["testdata_path"].endswith(
        "nexios-bsp-initramfs-apollo-fvp.testdata.json"
    )
