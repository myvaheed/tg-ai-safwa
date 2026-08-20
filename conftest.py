"""Root conftest: puts the repository on the path and registers the snapshot switch.

`tests/test_architecture.py` imports `scripts/architecture_metrics.py` so the rules and the
batch report come from one scanner rather than two copies of the same AST walk.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="rewrite the prompt and schema snapshots; only inside a batch declared to change them",
    )
