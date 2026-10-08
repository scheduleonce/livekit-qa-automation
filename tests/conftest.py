import pytest
import yaml
import glob
import os
import asyncio
import gc
import warnings
import configparser
from pathlib import Path
import re

from src.config import get_target_environment

target_env = get_target_environment()

def pytest_generate_tests(metafunc):
    if "scenario" in metafunc.fixturenames:
        scenarios = []
        base_dir = os.path.dirname(os.path.dirname(__file__))

        yaml_files = glob.glob(
            os.path.join(
                base_dir,
                "data/testDataToRun",
                "**",
                "*.yaml",
            ),
            recursive=True,
        )

        for file in yaml_files:
            with open(file, "r") as f:
                data = yaml.safe_load(f)

                # Preserve the source file for per-file configuration.
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            item["_source_file"] = file

                    scenarios.extend(data)
                else:
                    if isinstance(data, dict):
                        data["_source_file"] = file

                    scenarios.append(data)

        metafunc.parametrize(
            "scenario",
            scenarios,
            ids=[s["id"] for s in scenarios],
        )


@pytest.fixture(autouse=True)
def cleanup_after_test():
    """Cleanup resources after each test."""
    yield

    gc.collect()

    # Preserve the existing cleanup behavior.
    try:
        loop = asyncio.get_event_loop()

        if loop.is_running():
            pending = asyncio.all_tasks(loop)

            for task in pending:
                task.cancel()
    except RuntimeError:
        pass


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """Attach the existing conversation transcript to the Allure report."""
    outcome = yield
    rep = outcome.get_result()

    # Attach only after the test body.
    if rep.when != "call":
        return

    scenario = item.funcargs.get("scenario")
    if not isinstance(scenario, dict):
        return

    test_id = str(scenario.get("id", "unknown"))

    transcripts_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "transcripts",
    )

    # Original exporter filename format:
    # transcript_<environment>_<test_id>_<timestamp>.txt
    pattern = os.path.join(
        transcripts_dir,
        f"transcript_*_{test_id}_*.txt",
    )

    matches = glob.glob(pattern)
    if not matches:
        return

    # Preserve the original selection: newest matching transcript.
    matches.sort(key=os.path.getmtime, reverse=True)
    transcript_path = matches[0]

    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except Exception as exc:
        print(f"Transcript read failed for {test_id}: {exc}")
        return

    # Attach the clean Agent/Visitor conversation to Allure.
    if item.config.pluginmanager.hasplugin("allure_pytest"):
        try:
            import allure

            allure.attach(
                content,
                name=f"Conversation Transcript: {target_env}_{test_id}",
                attachment_type=allure.attachment_type.TEXT,
            )
        except Exception as exc:
            print(f"Allure transcript attachment failed: {exc}")