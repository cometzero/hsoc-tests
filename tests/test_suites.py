from __future__ import annotations

from apollo_validation.suites import list_suites


def test_suite_categories_are_visible() -> None:
    result = list_suites()

    assert set(result["categories"]) == {"basic", "functional", "power", "extended", "stress"}
    assert result["machine"] == "apollo-fvp"
    assert result["rd_aspen_variant"] == "cfg2"


def test_long_tests_are_not_in_basic() -> None:
    result = list_suites()
    basic_names = {entry["name"] for entry in result["categories"]["basic"]}

    assert "test_100_fwu" not in basic_names
    assert "test_60_cpuidle_cstates" not in basic_names
    assert "test_60_cpu_frequency" not in basic_names


def test_extended_and_stress_are_opt_in() -> None:
    result = list_suites()
    extended = result["categories"]["extended"]
    power = result["categories"]["power"]
    stress = result["categories"]["stress"]
    functional_names = {entry["name"] for entry in result["categories"]["functional"]}
    extended_names = {entry["name"] for entry in extended}

    assert any(entry["name"] == "test_100_fwu" for entry in extended)
    assert any(entry["name"] == "test_00_rse.RseTest.test_scmi_reboot" for entry in power)
    assert any(entry["name"] == "scmi_reboot_loop" for entry in stress)
    assert "test_02_safety_boot" not in functional_names
    assert "test_02_safety_boot.TestSafetyBoot.test_lbist" in extended_names
    assert "test_02_safety_boot.TestSafetyBoot.test_mbist" in extended_names
    assert all(entry.get("default") == "disabled" for entry in power)
    assert all(entry.get("default") == "disabled" for entry in extended)
    assert all(entry.get("default") == "disabled" for entry in stress)
