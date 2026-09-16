from __future__ import annotations

import pytest

_ALLOWED_CI_SKIPS = frozenset(
    {
        "tests/integration/test_live_groq.py::"
        "test_live_groq_structured_completion_normalizes_without_reasoning",
    }
)
_OBSERVED_SKIPS: set[str] = set()


def _ci_skip_policy_violations(
    observed: set[str],
) -> tuple[frozenset[str], frozenset[str]]:
    unexpected = frozenset(observed - _ALLOWED_CI_SKIPS)
    missing = frozenset(_ALLOWED_CI_SKIPS - observed)
    return unexpected, missing


def _record_skipped_report(
    report: pytest.CollectReport | pytest.TestReport,
    observed: set[str],
) -> None:
    if report.skipped and not getattr(report, "wasxfail", None):
        observed.add(report.nodeid)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("news-ai-ci")
    group.addoption(
        "--enforce-ci-skip-policy",
        action="store_true",
        default=False,
        help=(
            "Fail unless the only skipped test is the explicitly allowed live Groq acceptance test."
        ),
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    _OBSERVED_SKIPS.clear()


def pytest_collectreport(report: pytest.CollectReport) -> None:
    _record_skipped_report(report, _OBSERVED_SKIPS)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _record_skipped_report(report, _OBSERVED_SKIPS)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del exitstatus
    if not session.config.getoption("--enforce-ci-skip-policy"):
        return

    unexpected, missing = _ci_skip_policy_violations(_OBSERVED_SKIPS)
    if not unexpected and not missing:
        return

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", "CI skip policy violation")
        if unexpected:
            reporter.write_line("Unexpected skipped tests:")
            for nodeid in sorted(unexpected):
                reporter.write_line(f"  - {nodeid}")
        if missing:
            reporter.write_line("Expected CI skip was not observed:")
            for nodeid in sorted(missing):
                reporter.write_line(f"  - {nodeid}")

    session.exitstatus = pytest.ExitCode.TESTS_FAILED
