from __future__ import annotations

from pathlib import Path

import pytest

from apollo_validation.profiles import ProfileError, load_test_profile
from apollo_validation.root_cli import parse_root_args
from apollo_validation.selection import prepare_selection, write_selection_evidence


WORKSPACE = Path(__file__).resolve().parents[3]


def test_pfdi_profile_selects_bsp_oeqa_contract() -> None:
    # Given: the repository-owned PFDI test profile.
    # When: it is parsed for the FVP BSP backend.
    profile = load_test_profile(WORKSPACE, "pfdi", "fvp", "bsp")

    # Then: profile data resolves to executable OEQA selectors and target.
    assert profile.name == "pfdi"
    assert profile.selectors == ("test_00_bsp_boot", "test_64_bsp_pfdi")
    assert profile.oeqa_kind == "extended"
    assert profile.test_target == "HSOCBSPFVPTarget"
    assert profile.timeout_seconds == 1800


def test_profile_selection_applies_timeout_and_writes_snapshot(
    tmp_path: Path,
) -> None:
    # Given: the public profile command without an explicit timeout override.
    options = parse_root_args(
        ["--fvp", "--bsp", "--headless", "--test-profile", "pfdi"]
    )

    # When: the profile is resolved and its evidence is written.
    selection, resolved = prepare_selection(WORKSPACE, options)
    assert selection is not None
    write_selection_evidence(tmp_path, selection)

    # Then: OEQA selection, target, timeout, and profile snapshot agree.
    assert selection.ordered_tests == ("test_00_bsp_boot", "test_64_bsp_pfdi")
    assert selection.test_target == "HSOCBSPFVPTarget"
    assert resolved.category == "functional"
    assert resolved.timeout_oeqa == 1800
    assert (tmp_path / "resolved-profile.yaml").is_file()


def test_si_cl1_profile_selects_full_safety_island_suite() -> None:
    # Given: the Arm validation profile for Safety Island CL1 PFDI.
    # When: it is resolved for the FVP BSP backend.
    profile = load_test_profile(WORKSPACE, "pfdi-si-cl1", "fvp", "bsp")

    # Then: the BSP boot and complete SI CL1 OEQA module are selected.
    assert profile.selectors == ("test_00_bsp_boot", "test_30_si_cl1_pfdi")
    assert profile.test_target == "HSOCBSPFVPTarget"
    assert profile.timeout_seconds == 1800


def test_unknown_profile_reports_typed_error() -> None:
    # Given: a profile name with no repository definition.
    # When/Then: the loader reports its typed boundary error cleanly.
    with pytest.raises(ProfileError, match="unknown test profile: missing"):
        load_test_profile(WORKSPACE, "missing", "fvp", "bsp")


def test_safety_diagnostics_profile_selects_ssu_fmu_suite() -> None:
    # Given: the Arm Safety Island diagnostics validation profile.
    # When: it is resolved for the FVP BSP backend.
    profile = load_test_profile(
        WORKSPACE,
        "safety-diagnostics-tests",
        "fvp",
        "bsp",
    )

    # Then: the BSP boot and SSU/FMU OEQA module are selected.
    assert profile.selectors == (
        "test_00_bsp_boot",
        "test_20_si_cl0_diagnostics",
    )
    assert profile.test_target == "HSOCBSPFVPTarget"
    assert profile.timeout_seconds == 1800


def test_ras_cpu_profile_selects_complete_product_suite() -> None:
    # Given: the Primary Compute CPU RAS validation profile.
    # When: it is resolved for the FVP product-image backend.
    profile = load_test_profile(WORKSPACE, "ras_cpu", "fvp", "product")

    # Then: every boot dependency and the complete RAS module are selected.
    assert profile.selectors == (
        "test_00_fvp_boot.FVPBootTest.test_fvp_boot",
        "test_00_rse_boot.RseBootTest.test_normal_boot",
        "test_00_tfa_secure_partition_boot."
        "TfaSecurePartitionBootTest.test_secure_partition_boot",
        "test_00_linux_boot.LinuxBootTest.test_linux_boot",
        "test_40_tfa_cpu_topology."
        "TfaCpuTopologyTest.test_configured_pc_cpus_in_tfa",
        "test_41_tfa_ras",
    )
    assert profile.test_target == "HSOCSingleSessionFVPTarget"
    assert profile.timeout_seconds == 3600
