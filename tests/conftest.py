"""Tests always use their own temporary data and runtime paths."""
import pytest


@pytest.fixture(autouse=True)
def isolate_runtime_environment(monkeypatch):
    for name in ("XHS_LOCAL_DATA", "XHS_RUNTIME_ROOT", "XHS_MEDIA_ROOT", "XHS_COOKIE_FILE", "XHS_SPIDER_ROOT"):
        monkeypatch.delenv(name, raising=False)
