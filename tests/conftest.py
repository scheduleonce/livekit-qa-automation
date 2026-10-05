import pytest
import yaml
import glob
import os
import asyncio
import gc
import warnings
import configparser
from html import escape
from pathlib import Path
import re  # add near other imports

#from src.llm_performance_analyzer import build_smart_agent_performance_report

# def pytest_addoption(parser):
#     parser.addini("generate_agent_performance_report", "Generate the agent performance report at pytest session end", type="bool", default=True)


def pytest_generate_tests(metafunc):
    if "scenario" in metafunc.fixturenames:
        scenarios = []
        base_dir = os.path.dirname(os.path.dirname(__file__))
        yaml_files = glob.glob(os.path.join(base_dir, "data/testDataToRun", "**", "*.yaml"), recursive=True)
        
        for file in yaml_files:
            with open(file, 'r') as f:
                data = yaml.safe_load(f)
                # attach the source filename to each scenario so runtime code
                # can use per-file settings (like environments -> target_bot_id)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            item['_source_file'] = file
                    scenarios.extend(data)
                else:
                    if isinstance(data, dict):
                        data['_source_file'] = file
                    scenarios.append(data)
                
        metafunc.parametrize("scenario", scenarios, ids=[s['id'] for s in scenarios])


@pytest.fixture(autouse=True)
def cleanup_after_test():
    """Cleanup resources after each test to prevent interference between tests."""
    yield
    # Force garbage collection to clean up AsyncClient connections
    gc.collect()
    # Give event loop a chance to close lingering tasks
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
    except RuntimeError:
        pass  # No event loop in current thread


# def pytest_sessionfinish(session, exitstatus):
#     """Generate a smart agent performance report after pytest completes."""
#     config = session.config
#     setting = "true"
#     if config.inipath:
#         ini_config = configparser.ConfigParser()
#         ini_config.read(config.inipath, encoding="utf-8")
#         setting = ini_config.get(
#             "tool:pytest", "generate_agent_performance_report", fallback="true"
#         )
#     if str(setting).strip().lower() not in {"1", "true", "yes", "on"}:
#         return

#     base_dir = Path(__file__).resolve().parents[1]
#     transcript_dir = base_dir / "transcripts"
#     output_path = base_dir / "reports" / "smart_agent_performance_report.csv"

#     try:
#         build_smart_agent_performance_report(transcript_dir, output_path)
#         warnings.warn(f"Smart agent performance report written to {output_path}", stacklevel=2)
#     except Exception as exc:
#         warnings.warn(f"Failed to build smart agent performance report: {exc}", stacklevel=2)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach the current test's transcript to HTML and Allure reports."""
    outcome = yield
    rep = outcome.get_result()

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

    # Existing exporter filename format:
    # transcript_<environment>_<test_id>_<timestamp>.txt
    pattern = os.path.join(
        transcripts_dir,
        f"transcript_*_{glob.escape(test_id)}_*.txt",
    )

    # Only consider files written during this test.
    # This avoids attaching an old transcript when the current test fails
    # before generating one.
    matches = []
    for path in glob.glob(pattern):
        try:
            if os.path.getmtime(path) >= call.start:
                matches.append(path)
        except OSError:
            continue

    if not matches:
        return

    transcript_path = max(matches, key=os.path.getmtime)

    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        warnings.warn(
            f"Unable to read transcript for {test_id}: {exc}",
            stacklevel=2,
        )
        return

    # Attach the clean conversation to Allure.
    # Skip when the Allure adapter is disabled or unavailable.
    if item.config.pluginmanager.hasplugin("allure_pytest"):
        try:
            import allure

            allure.attach(
                content,
                name=f"Conversation Transcript: {test_id}",
                attachment_type=allure.attachment_type.TEXT,
            )
        except Exception as exc:
            warnings.warn(
                f"Unable to attach Allure transcript for {test_id}: {exc}",
                stacklevel=2,
            )

    # Preserve the existing pytest-html transcript.
    html_plugin = item.config.pluginmanager.getplugin("html")
    if html_plugin:
        try:
            rep.extras = getattr(rep, "extras", []) or []
            rep.extras.append(
                html_plugin.extras.html(
                    f"<h3>Transcript: {escape(test_id)}</h3>"
                    f"<pre>{escape(content)}</pre>"
                )
            )
        except Exception as exc:
            warnings.warn(
                f"Unable to attach HTML transcript for {test_id}: {exc}",
                stacklevel=2,
            )