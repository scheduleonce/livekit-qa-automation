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
        yaml_files = glob.glob(os.path.join(base_dir, "data", "*.yaml"))
        
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
    """Attach end-to-end transcript (if present) to the pytest-html report extras.

    Looks for transcript files created by `TranscriptExporter.save` using the
    pattern `transcript_{test_id}_*.txt` in the `transcripts/` directory and
    embeds the latest one as an HTML `<pre>` block in the report.
    """
    outcome = yield
    rep = outcome.get_result()
    # only attach for the call phase (test body)
    if rep.when != "call":
        return

    # try to get scenario id from parametrized fixture
    scenario = None
    try:
        scenario = item.funcargs.get("scenario")
    except Exception:
        scenario = None

    if not scenario:
        return

    test_id = str(scenario.get("id", "unknown"))
    transcripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "transcripts")
    # TranscriptExporter includes the environment between the prefix and test ID.
    pattern = os.path.join(transcripts_dir, f"transcript_*_{test_id}_*.txt")
    matches = glob.glob(pattern)
    if not matches:
        return

    # pick the newest transcript file
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    transcript_path = matches[0]

    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except Exception:
        return

    # attach as HTML extra if pytest-html plugin is available
    html_plugin = item.config.pluginmanager.getplugin("html")
    if not html_plugin:
        return

    extras_attribute = "extras" if hasattr(rep, "extras") else "extra"
    extras = getattr(rep, extras_attribute, []) or []
    try:
        extras.append(html_plugin.extras.html(f"<h3>Transcript: {escape(test_id)}</h3><pre>{escape(content)}</pre>"))
        setattr(rep, extras_attribute, extras)
    except Exception:
        # best-effort: ignore errors attaching extras
        pass