"""Discover composition-local ALZ tests through the existing test runner."""

import importlib.util
from pathlib import Path


def load_tests(loader, tests, pattern):
    path = Path(__file__).resolve().parents[1] / "apis/azure/landingzone/tests/test_landingzone.py"
    spec = importlib.util.spec_from_file_location("landingzone_local_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return loader.loadTestsFromModule(module)
