"""Tidy the Python code and run the same checks as CI.

    uv run python scripts/check.py                   # on your machine
    docker compose exec api python scripts/check.py  # inside Docker

Lint problems and formatting are fixed for you; anything left is printed.
"""

import subprocess
import sys

STEPS = [
    ("Fixing lint", ["ruff", "check", "--fix", "."]),
    ("Formatting", ["ruff", "format", "."]),
    ("Running tests", ["pytest", "-q"]),
]

for title, args in STEPS:
    print(f"\n==> {title}", flush=True)
    if subprocess.run([sys.executable, "-m", *args]).returncode != 0:
        print(f"\nFAILED: {title}. Fix the problems above, then run this again.")
        sys.exit(1)

print("\nAll checks passed.")
