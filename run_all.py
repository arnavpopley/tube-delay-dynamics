"""Rebuild artefacts that currently exist.

Phase 2+ scripts are not implemented. Do not add reconstruction, headway
fitting, or models here until a full day of data exists and the Phase 1
manual sequence check has been done by hand.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ingest = ROOT / "src" / "ingest.py"
    result = subprocess.run([sys.executable, str(ingest), "all"], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
