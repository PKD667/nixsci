"""A test that did not run proves nothing, so a skip is a failure.

Opt-in switches, missing optional packages and absent tools all report green when skipped, and a green suite that
skipped its hardest tests is how a regression gets through. A test gets what it needs, or is left out where the
runner is configured (flake.nix, `--ignore` with the reason), where a reader can see it is not run."""
import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.skipped and not hasattr(report, "wasxfail"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        report.outcome = "failed"
        report.longrepr = f"skipped, which is not allowed: {reason}"
