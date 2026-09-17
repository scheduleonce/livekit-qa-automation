import json

import pytest

from src.config import get_target_environment, resolve_credentials


def _write_local_config(tmp_path):
    json_path = tmp_path / "livekit_bot_config.json"
    yaml_path = tmp_path / "scenario.yaml"
    json_path.write_text(
        json.dumps(
            {
                "App3": {
                    "LIVEKIT_URL": "wss://json.example",
                    "API_KEY": "json-key",
                    "API_SECRET": "json-secret",
                    "BOT_ID": [{"id": "1", "value": "json-bot"}],
                }
            }
        ),
        encoding="utf-8",
    )
    yaml_path.write_text(
        "environments:\n  App3:\n    target_bot_id: '1'\n",
        encoding="utf-8",
    )
    return json_path, yaml_path


def test_environment_credentials_override_json(monkeypatch, tmp_path):
    json_path, yaml_path = _write_local_config(tmp_path)
    monkeypatch.setenv("LIVEKIT_URL", "wss://jenkins.example")
    monkeypatch.setenv("API_KEY", "jenkins-key")
    monkeypatch.setenv("API_SECRET", "jenkins-secret")
    monkeypatch.setenv("BOT_ID", "jenkins-bot")

    result = resolve_credentials("App3", json_path, yaml_path)

    assert result == (
        "wss://jenkins.example",
        "jenkins-key",
        "jenkins-secret",
        "jenkins-bot",
    )


def test_local_json_credentials_and_bot_mapping_are_fallback(monkeypatch, tmp_path):
    json_path, yaml_path = _write_local_config(tmp_path)
    for name in ("LIVEKIT_URL", "API_KEY", "API_SECRET", "BOT_ID"):
        monkeypatch.delenv(name, raising=False)

    assert resolve_credentials("App3", json_path, yaml_path) == (
        "wss://json.example",
        "json-key",
        "json-secret",
        "json-bot",
    )


def test_partial_environment_credentials_fail_fast(monkeypatch, tmp_path):
    json_path, yaml_path = _write_local_config(tmp_path)
    monkeypatch.setenv("LIVEKIT_URL", "wss://jenkins.example")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("API_SECRET", raising=False)

    with pytest.raises(ValueError, match="Incomplete environment credentials"):
        resolve_credentials("App3", json_path, yaml_path)


def test_environment_selector_precedence(monkeypatch):
    monkeypatch.setenv("APP_ENV", "Orion")
    monkeypatch.setenv("TEST_ENV", "App2")
    monkeypatch.setenv("ENV", "App3")

    assert get_target_environment() == "Orion"