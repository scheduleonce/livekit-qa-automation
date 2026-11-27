import pytest
import yaml
import glob
import os
import asyncio
import gc
import logging
from pathlib import Path

from src.config import get_target_environment
from src.scenario_loader import load_scenarios

target_env = get_target_environment()


class _IgnoreUnwantedLogs(logging.Filter):
    IGNORED_MESSAGES = {
        "ignoring byte stream with topic 'lk.agent.session', no callback attached",
        "ignoring text stream with topic 'lk.transcription', no callback attached",
    }

    def filter(self, record):
        return not (
            record.levelno == logging.INFO
            and record.getMessage() in self.IGNORED_MESSAGES
        )


def pytest_configure(config):
    logging.getLogger().addFilter(_IgnoreUnwantedLogs())


def pytest_addoption(parser):
    parser.addoption(
        "--scenario-group",
        default=None,
        help="Only collect scenarios from this folder under data/testDataToRun",
    )
    parser.addoption(
        "--scenario-file",
        default=None,
        help="Run scenarios from one YAML file instead of discovering all scenario files",
    )


def pytest_generate_tests(metafunc):
    if "scenario" in metafunc.fixturenames:
        scenario_root = Path(__file__).resolve().parent.parent / "data" / "testDataToRun"
        selected_group = metafunc.config.getoption("--scenario-group")
        selected_file = metafunc.config.getoption("--scenario-file")
        try:
            scenarios, _ = load_scenarios(
                scenario_root,
                selected_group=selected_group,
                scenario_file=selected_file,
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise pytest.UsageError(str(exc)) from exc

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