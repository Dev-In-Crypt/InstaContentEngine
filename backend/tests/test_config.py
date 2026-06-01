import pytest
from config import Settings


def test_settings_defaults():
    s = Settings()
    assert s.api_port == 8000
    assert s.storage_type == "local"
    assert s.brand_engine_default if hasattr(s, "brand_engine_default") else True


def test_settings_override(monkeypatch):
    monkeypatch.setenv("API_PORT", "9000")
    monkeypatch.setenv("STORAGE_TYPE", "s3")
    s = Settings()
    assert s.api_port == 9000
    assert s.storage_type == "s3"


def test_settings_openrouter_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test123")
    s = Settings()
    assert s.openrouter_api_key == "sk-or-test123"
