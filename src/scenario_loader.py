import copy
import json
import re
from pathlib import Path
from typing import Any

import yaml


_DATASET_KEYS = ("dataset", "datasets", "parameters")
_TEMPLATE_PATTERN = re.compile(r"{{\s*(?:testData\.)?([^{}]+?)\s*}}")


def _read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as scenario_file:
        return yaml.safe_load(scenario_file)


def _read_dataset_file(path: Path) -> Any:
    if not path.is_file():
        raise ValueError(f"Dataset file not found: {path}")

    suffix = path.suffix.casefold()
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as dataset_file:
            return json.load(dataset_file)
    if suffix in (".yaml", ".yml"):
        return _read_yaml(path)
    raise ValueError(
        f"Unsupported dataset file extension for {path}; use .json, .yaml, or .yml"
    )


def _as_dataset_rows(value: Any, scenario_path: Path) -> list[dict[str, Any]]:
    if isinstance(value, str):
        dataset_path = (scenario_path.parent / value).resolve()
        value = _read_dataset_file(dataset_path)
    elif isinstance(value, dict) and "file" in value:
        if len(value) != 1 or not isinstance(value["file"], str):
            raise ValueError(
                f"'datasets.file' in {scenario_path} must be a string and cannot "
                "be combined with other dataset fields"
            )
        value = _read_dataset_file((scenario_path.parent / value["file"]).resolve())

    if isinstance(value, dict):
        if len(value) == 1 and next(iter(value)) in _DATASET_KEYS:
            value = value[next(iter(value))]
        else:
            named_rows = []
            for dataset_id, row in value.items():
                if not isinstance(row, dict):
                    raise ValueError(
                        f"Named dataset {dataset_id!r} in {scenario_path} must be an object"
                    )
                named_rows.append({"id": str(dataset_id), **row})
            value = named_rows

    if not isinstance(value, list) or not value:
        raise ValueError(
            f"Datasets in {scenario_path} must resolve to a non-empty list of objects"
        )
    if any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Every dataset row in {scenario_path} must be an object")
    return value


def _substitute_test_data(value: Any, test_data: dict[str, Any]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            if key not in test_data:
                raise ValueError(f"Scenario template references missing testData key {key!r}")
            return str(test_data[key])

        return _TEMPLATE_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_substitute_test_data(item, test_data) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                item if key == "testData"
                else _substitute_test_data(item, test_data)
            )
            for key, item in value.items()
        }
    return value


def _expand_scenario(
    scenario: dict[str, Any],
    scenario_path: Path,
    source_group: str,
) -> list[dict[str, Any]]:
    if "id" not in scenario:
        raise ValueError(f"Scenario in {scenario_path} is missing required 'id'")

    declared_keys = [key for key in _DATASET_KEYS if key in scenario]
    if len(declared_keys) > 1:
        raise ValueError(
            f"Scenario {scenario['id']!r} in {scenario_path} must use only one "
            "of 'dataset', 'datasets', or 'parameters'"
        )
    if not declared_keys:
        rows = [None]
    else:
        rows = _as_dataset_rows(scenario[declared_keys[0]], scenario_path)

    expanded = []
    for index, row in enumerate(rows, start=1):
        result = copy.deepcopy(scenario)
        for key in _DATASET_KEYS:
            result.pop(key, None)

        base_test_data = result.get("testData", {})
        if not isinstance(base_test_data, dict):
            raise ValueError(
                f"'testData' for scenario {scenario['id']!r} in {scenario_path} "
                "must be an object"
            )

        if row is not None:
            dataset_id = row.get("id")
            if "testData" in row:
                overrides = row["testData"]
                if not isinstance(overrides, dict):
                    raise ValueError(
                        f"'testData' in dataset {dataset_id or index!r} for "
                        f"{scenario_path} must be an object"
                    )
            else:
                overrides = {key: value for key, value in row.items() if key != "id"}
            result["testData"] = {**base_test_data, **overrides}

            if dataset_id is not None:
                suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", str(dataset_id)).strip("_")
                if not suffix:
                    raise ValueError(
                        f"Dataset id in scenario {scenario['id']!r} must contain "
                        "at least one letter or number"
                    )
                result["id"] = f"{scenario['id']}_{suffix}"
            else:
                result["id"] = f"{scenario['id']}_dataset_{index}"

        result = _substitute_test_data(result, result.get("testData", {}))
        result["_source_file"] = str(scenario_path)
        result["_source_group"] = source_group
        expanded.append(result)

    ids = [item["id"] for item in expanded]
    if len(ids) != len(set(ids)):
        raise ValueError(
            f"Dataset IDs for scenario {scenario['id']!r} in {scenario_path} "
            "must be unique"
        )
    return expanded


def _scenario_group(scenario: dict[str, Any], scenario_path: Path, root: Path) -> str:
    if scenario.get("group"):
        return str(scenario["group"])
    try:
        relative_parts = scenario_path.relative_to(root).parts
    except ValueError:
        relative_parts = ()
    return relative_parts[0] if len(relative_parts) > 1 else "Ungrouped"


def load_scenarios(
    scenario_root: Path,
    selected_group: str | None = None,
    scenario_file: str | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    root = scenario_root.resolve()
    if scenario_file:
        selected_path = Path(scenario_file).expanduser()
        if not selected_path.is_absolute():
            selected_path = Path.cwd() / selected_path
        files = [selected_path.resolve()]
        if selected_group:
            raise ValueError("--scenario-group cannot be combined with --scenario-file")
    else:
        files = sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml"))

    scenarios = []
    available_groups: set[str] = set()
    for path in files:
        if not path.is_file():
            raise ValueError(f"Scenario file not found: {path}")
        data = _read_yaml(path)
        items = data if isinstance(data, list) else [data]
        if not items or any(not isinstance(item, dict) for item in items):
            raise ValueError(f"Scenario file {path} must contain an object or a list of objects")

        for item in items:
            group = _scenario_group(item, path, root)
            available_groups.add(group)
            if selected_group and group.casefold() != selected_group.casefold():
                continue
            scenarios.extend(_expand_scenario(item, path, group))

    if selected_group and not scenarios:
        groups = ", ".join(sorted(available_groups)) or "none"
        raise ValueError(
            f"No scenarios found for group {selected_group!r}. Available groups: {groups}"
        )
    return scenarios, available_groups
