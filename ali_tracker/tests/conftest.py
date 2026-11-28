"""בבדיקות לא פונים לבנק ישראל ולא כותבים config.json ליד הקוד."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_network_or_config(monkeypatch, request):
    from ali_tracker.config import Config
    monkeypatch.setattr(Config, "save", lambda self: None)
    if "real_rates" in request.keywords:
        return
    try:
        from ali_tracker.gui.rates import RateFetcher
    except ImportError:                       # אין PySide6 (או ספריות מערכת): בדיקות הממשק מדולגות
        return
    monkeypatch.setattr(RateFetcher, "fetch", lambda self: None)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_rates: let the window fetch exchange rates")
