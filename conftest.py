"""A test that did not run proves nothing, so a skip is a failure.

Opt-in switches, missing optional packages and absent tools all report green when skipped, and a green suite that
skipped its hardest tests is how a regression gets through. A test gets what it needs, or is left out where the
runner is configured (flake.nix, `--ignore` with the reason), where a reader can see it is not run.

The checkout is also made importable as the `nixsci` package here, so running the tests needs no PYTHONPATH and no
particular name for the directory it was cloned into."""
import os
import sys
import tempfile
from pathlib import Path

import pytest


def _parent_of_nixsci() -> Path:
    """A directory that holds this checkout under the name `nixsci`: its own parent, or a link in a scratch one."""
    root = Path(__file__).resolve().parent
    if root.name == "nixsci":
        return root.parent
    link = Path(tempfile.mkdtemp(prefix="nixsci-import-")) / "nixsci"
    link.symlink_to(root, target_is_directory=True)
    return link.parent


_parent = str(_parent_of_nixsci())
sys.path.insert(0, _parent)
os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [_parent, os.environ.get("PYTHONPATH")]))  # for the tests' subprocesses


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.skipped and not hasattr(report, "wasxfail"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        report.outcome = "failed"
        report.longrepr = f"skipped, which is not allowed: {reason}"
