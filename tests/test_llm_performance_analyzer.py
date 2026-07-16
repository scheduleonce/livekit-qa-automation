import json
from pathlib import Path

import pytest

from src.llm_performance_analyzer import (
    VALID_AGENT_NAMES,
    aggregate_latencies_by_agent,
    classify_agent_llm,
    extract_spoken_text_latency,
    generate_smart_agent_performance_report,
    parse_transcript_directory,
)


def test_extract_spoken_text_latency():
    transcript = "Agent: Booked appointment [Latency = 1.45]\nUser: Thanks [Latency = 0.33]"
    results = extract_spoken_text_latency(transcript)
    assert results == [
        ("Agent: Booked appointment", 1.45),
        ("User: Thanks", 0.33),
    ]


def test_parse_transcript_directory(tmp_path: Path):
    transcript_file = tmp_path / "sample.txt"
    transcript_file.write_text("Hello there [Latency = 0.82]\nAnother line [Latency = 1.20]", encoding="utf-8")

    entries = parse_transcript_directory(tmp_path)
    assert len(entries) == 2
    assert entries[0]["spoken_text"] == "Hello there"
    assert entries[0]["latency"] == 0.82
    assert entries[0]["source_file"] == "sample.txt"


def test_generate_smart_agent_performance_report(tmp_path: Path):
    aggregated = {
        "Intent_Detection": [0.12, 0.35],
        "Unknown": [0.55],
    }
    report_path = tmp_path / "report.csv"
    output_path = generate_smart_agent_performance_report(aggregated, report_path)

    assert output_path.exists()
    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Agent Name,Avg Latency,Max Latency,Total Calls"
    assert any("Intent_Detection" in line for line in lines)
    assert any("Unknown" in line for line in lines)


def test_classify_agent_llm_returns_valid_name(monkeypatch):
    def fake_create(*args, **kwargs):
        class FakeChoice:
            def __init__(self):
                self.message = type("Msg", (), {"content": '{"agent_name": "Intent_Detection"}'})

        class FakeResponse:
            def __init__(self):
                self.choices = [FakeChoice()]

        return FakeResponse()

    class FakeResponse:
        def __init__(self):
            self.choices = [
                type("Choice", (), {"message": type("Msg", (), {"content": '{"agent_name": "Intent_Detection"}'})})()
            ]

    class FakeChat:
        @property
        def completions(self):
            return self

        def create(self, *args, **kwargs):
            return FakeResponse()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.chat = FakeChat()

    monkeypatch.setattr("src.llm_performance_analyzer._get_azure_openai_client", lambda: FakeClient())
    classification = classify_agent_llm("Check booking availability")
    assert classification["agent_name"] == "Intent_Detection"
    assert classification["agent_name"] in VALID_AGENT_NAMES


def test_aggregate_latencies_by_agent(monkeypatch):
    def fake_classify_agent_llm(text):
        return {"agent_name": "Booking_Hub"}

    monkeypatch.setattr("src.llm_performance_analyzer.classify_agent_llm", fake_classify_agent_llm)
    entries = [
        {"spoken_text": "Start booking", "latency": 0.9},
        {"spoken_text": "Continue booking", "latency": 1.1},
        {"spoken_text": "Invalid line", "latency": None},
    ]

    aggregated = aggregate_latencies_by_agent(entries)
    assert aggregated["Booking_Hub"] == [0.9, 1.1]
