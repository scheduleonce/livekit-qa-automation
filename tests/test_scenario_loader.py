import json

import pytest

from src.scenario_loader import load_scenarios

pytestmark = pytest.mark.config


def _scenario_yaml(test_data="  name: Original\n  email: original@example.com\n"):
    return (
        "id: TC_BOOKING\n"
        "objective: Book a meeting as {{testData.name}} at {{email}}.\n"
        "testData:\n"
        f"{test_data}"
        "expectedOutcomes: []\n"
    )


def test_standalone_scenario_remains_one_execution(tmp_path):
    root = tmp_path / "scenarios"
    scenario_file = root / "simple-booking" / "booking.yaml"
    scenario_file.parent.mkdir(parents=True)
    scenario_file.write_text(_scenario_yaml(), encoding="utf-8")

    scenarios, _ = load_scenarios(root)

    assert len(scenarios) == 1
    assert scenarios[0]["id"] == "TC_BOOKING"
    assert scenarios[0]["testData"] == {
        "name": "Original",
        "email": "original@example.com",
    }
    assert scenarios[0]["objective"] == "Book a meeting as Original at original@example.com."


def test_inline_parameters_merge_test_data_and_create_unique_ids(tmp_path):
    root = tmp_path / "scenarios"
    scenario_file = root / "booking.yaml"
    scenario_file.parent.mkdir(parents=True)
    scenario_file.write_text(
        _scenario_yaml()
        + "parameters:\n"
        + "  - id: first\n"
        + "    testData:\n"
        + "      name: Alex\n"
        + "      email: alex@example.com\n"
        + "  - id: second\n"
        + "    testData:\n"
        + "      name: Sam\n"
        + "      phone: '12345'\n",
        encoding="utf-8",
    )

    scenarios, _ = load_scenarios(root)

    assert [scenario["id"] for scenario in scenarios] == [
        "TC_BOOKING_first",
        "TC_BOOKING_second",
    ]
    assert scenarios[0]["testData"] == {
        "name": "Alex",
        "email": "alex@example.com",
    }
    assert scenarios[1]["testData"] == {
        "name": "Sam",
        "email": "original@example.com",
        "phone": "12345",
    }
    assert scenarios[1]["objective"] == "Book a meeting as Sam at original@example.com."


def test_external_json_datasets_can_be_selected_by_scenario_file(tmp_path):
    scenario_root = tmp_path / "scenarios"
    scenario_file = scenario_root / "booking.yaml"
    scenario_root.mkdir()
    (tmp_path / "users.json").write_text(
        json.dumps(
            [
                {"id": "alex", "name": "Alex", "email": "alex@example.com"},
                {"id": "sam", "name": "Sam", "email": "sam@example.com"},
            ]
        ),
        encoding="utf-8",
    )
    scenario_file.write_text(
        _scenario_yaml() + "datasets:\n  file: ../users.json\n",
        encoding="utf-8",
    )

    scenarios, _ = load_scenarios(
        scenario_root,
        scenario_file=str(scenario_file),
    )

    assert [scenario["id"] for scenario in scenarios] == [
        "TC_BOOKING_alex",
        "TC_BOOKING_sam",
    ]
    assert scenarios[1]["testData"]["email"] == "sam@example.com"
    assert scenarios[1]["objective"] == "Book a meeting as Sam at sam@example.com."
    assert scenarios[0]["_source_file"] == str(scenario_file.resolve())


def test_external_yaml_dataset_file(tmp_path):
    root = tmp_path / "scenarios"
    scenario_file = root / "booking.yaml"
    root.mkdir()
    (tmp_path / "users.yaml").write_text(
        "datasets:\n"
        "  - id: alex\n"
        "    testData:\n"
        "      name: Alex\n",
        encoding="utf-8",
    )
    scenario_file.write_text(
        _scenario_yaml() + "datasets:\n  file: ../users.yaml\n",
        encoding="utf-8",
    )

    scenarios, _ = load_scenarios(root)

    assert [scenario["id"] for scenario in scenarios] == ["TC_BOOKING_alex"]
    assert scenarios[0]["testData"]["name"] == "Alex"


def test_named_yaml_dataset_block_and_group_filter(tmp_path):
    root = tmp_path / "scenarios"
    scenario_file = root / "simple-booking" / "booking.yaml"
    scenario_file.parent.mkdir(parents=True)
    (root / "other").mkdir()
    scenario_file.write_text(
        _scenario_yaml()
        + "datasets:\n"
        + "  alex:\n"
        + "    testData:\n"
        + "      name: Alex\n"
        + "  sam:\n"
        + "    testData:\n"
        + "      name: Sam\n",
        encoding="utf-8",
    )
    (root / "other" / "ignored.yaml").write_text(
        "id: TC_OTHER\ntestData: {}\n",
        encoding="utf-8",
    )

    scenarios, _ = load_scenarios(root, selected_group="SIMPLE-BOOKING")

    assert [scenario["id"] for scenario in scenarios] == [
        "TC_BOOKING_alex",
        "TC_BOOKING_sam",
    ]
    assert {scenario["_source_group"] for scenario in scenarios} == {"simple-booking"}


def test_rejects_multiple_dataset_declarations_and_missing_template_keys(tmp_path):
    root = tmp_path / "scenarios"
    root.mkdir()
    scenario_file = root / "invalid.yaml"
    scenario_file.write_text(
        "id: TC_INVALID\n"
        "objective: Use {{testData.missing}}.\n"
        "testData: {}\n"
        "dataset: []\n"
        "parameters: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must use only one"):
        load_scenarios(root)

    scenario_file.write_text(
        "id: TC_INVALID\n"
        "objective: Use {{testData.missing}}.\n"
        "testData: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing testData key"):
        load_scenarios(root)
