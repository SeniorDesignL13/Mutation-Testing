"""CI's guards against tests that call a real judge (tests/conftest.py, MTLJ-90).

The marker guard is tested by running pytest in a fresh process with ``CI`` set, only collecting,
so nothing is called. The network guard is tested in place.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE = "tests/judges/deepeval/test_faithfulness_live.py"


def collect_in_ci(*args: str, ci: str = "true") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        env={**os.environ, "CI": ci},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_ci_collects_the_default_suite_without_live_tests():
    # Live tests are collected and then deselected by the default `-m "not integration"`. The
    # guard must look only at what's left, or every CI run would fail.
    result = collect_in_ci(LIVE)

    assert result.returncode in (0, 5), result.stdout + result.stderr  # 5: nothing left to run
    assert "call a real judge" not in result.stdout + result.stderr


def test_ci_refuses_live_tests_selected_on_purpose():
    result = collect_in_ci("-m", "integration", LIVE)

    assert result.returncode != 0
    assert "call a real judge" in result.stdout + result.stderr


def test_the_network_guard_refuses_other_machines(no_network):
    with pytest.raises(AssertionError, match="network connection was attempted"):
        socket.create_connection(("api.openai.com", 443), timeout=1)
    with pytest.raises(AssertionError, match="network connection was attempted"):
        socket.create_connection(("93.184.216.34", 443), timeout=1)


def test_the_network_guard_lets_asyncio_run(no_network):
    a, b = socket.socketpair()  # what asyncio's event loop needs, on every platform
    a.close()
    b.close()


def test_ci_false_is_not_ci():
    # A developer who exports CI=false can still run the live tests on purpose.
    result = collect_in_ci("-m", "integration", LIVE, ci="false")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "call a real judge" not in result.stdout + result.stderr


def test_outside_network_is_blocked_for_the_whole_run_in_ci():
    if os.environ.get("CI", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("checks the block CI installs; run by the next test")
    with pytest.raises(AssertionError, match="network connection was attempted"):
        socket.getaddrinfo("api.openai.com", 443)


def test_ci_blocks_the_network_without_any_fixture():
    node = "tests/test_ci_guard.py::test_outside_network_is_blocked_for_the_whole_run_in_ci"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
        cwd=ROOT,
        env={**os.environ, "CI": "true"},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
