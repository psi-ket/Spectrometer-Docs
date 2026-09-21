"""Test fixtures.

* ``mock_api``: installs the in-memory mock backend (no hardware, no library).
* ``real_api``: skips unless the real Swabian library is importable; restores it.
* ``demo_ttbin``: a short synthetic dataset generated once per session with the real library.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from swabian_backend import api as api_mod  # noqa: E402
from swabian_backend import mock as mock_mod  # noqa: E402


def _real_available() -> bool:
    api_mod.reset_api()
    a = api_mod.get_api()
    ok = a.available and not a.is_mock
    return ok


REAL_LIBRARY = _real_available()


@pytest.fixture
def mock_api():
    mock_mod.set_mock_devices({"MOCK-0001": "Time Tagger Ultra (mock)"})
    api = api_mod.set_api_module(mock_mod.build_mock(), version="2.22.6-mock", is_mock=True)
    yield api
    api_mod.reset_api()


@pytest.fixture
def real_api():
    if not REAL_LIBRARY:
        pytest.skip("Swabian TimeTagger library not installed")
    api_mod.reset_api()
    yield api_mod.get_api()


@pytest.fixture(scope="session")
def demo_ttbin(tmp_path_factory):
    if not REAL_LIBRARY:
        pytest.skip("Swabian TimeTagger library not installed")
    api_mod.reset_api()
    from simulator.demo_dataset import scenario_four_detector, generate_dataset
    out = tmp_path_factory.mktemp("demo")
    meta = generate_dataset(scenario_four_detector(2.0), out)
    return meta


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SNSPEC_HOME", str(tmp_path / "home"))
    return tmp_path / "home"
