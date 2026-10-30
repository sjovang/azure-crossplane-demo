"""Discover management-baseline tests without relocating legacy test modules."""
import importlib.util
from pathlib import Path


def load_tests(loader, tests, pattern):
    directory = (
        Path(__file__).resolve().parents[1]
        / "apis/environments/managementsubscription/tests"
    )
    for name in ["test_contract", "test_runtime"]:
        spec = importlib.util.spec_from_file_location(
            "management_baseline_" + name, directory / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tests.addTests(loader.loadTestsFromModule(module))
    return tests
