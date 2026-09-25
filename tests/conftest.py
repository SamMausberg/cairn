"""Make source-tree tests exercise the distributable package, and let `make gpu` collect only its device runs."""

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]


def pytest_addoption(parser):
    parser.addoption("--device-runs", action="store_true", help="collect only the tests that run device code")


def pytest_collection_modifyitems(config, items):
    """Under --device-runs, which `make gpu` passes, keep a test only when its body or a fixture it takes reaches a
    device run (emitted.DEVICE_RUNS): the owner's device session runs nothing the everyday suite already runs."""
    if not config.getoption("--device-runs"):
        return
    from emitted import DEVICE_RUNS

    def runs(item) -> bool:
        fixtures = [d.func for defs in item._fixtureinfo.name2fixturedefs.values() for d in defs]
        return any(DEVICE_RUNS.search(inspect.getsource(f)) for f in [item.function, *fixtures])

    kept: list = []
    dropped: list = []
    for item in items:
        (kept if runs(item) else dropped).append(item)
    config.hook.pytest_deselected(items=dropped)
    items[:] = kept
