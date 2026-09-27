import pytest


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    if reporter and (reporter.stats.get("skipped") or reporter.stats.get("xfailed")):
        session.exitstatus = 1
