#!/usr/bin/env python3
"""Compatibility entry; new projects use cairn test."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cairn.verify.testing import FLAGS, evaluate, main, validate_contract

__all__ = ["FLAGS", "evaluate", "main", "validate_contract"]

if __name__ == "__main__":
    raise SystemExit(main())
