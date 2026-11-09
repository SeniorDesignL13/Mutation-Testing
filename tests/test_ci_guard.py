"""CI's guard against tests that call a real judge (tests/conftest.py, MTLJ-90).

Each case runs pytest in a fresh process with ``CI`` set, only collecting, so nothing is called.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = "tests/judges/deepeval/test_faithfulness_live.py"


def collect_in_ci(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        env={**os.environ, "CI": "true"},
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
