"""Discover composition-local ALZ tests through the existing test runner."""

import importlib.util
from pathlib import Path


def load_tests(loader, tests, pattern):
    directory = Path(__file__).resolve().parents[1] / "apis/azure/landingzone/tests"
    for path in sorted(directory.glob("test_*.py")):
        spec = importlib.util.spec_from_file_location("landingzone_" + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tests.addTests(loader.loadTestsFromModule(module))
    return tests
