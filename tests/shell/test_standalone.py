"""The example is a whole application, not a corner of Safwa's.

An import graph shows that the shell imports no Safwa. This shows the other direction: a
process that cannot import Safwa at all still runs the whole path and builds a database
holding the shell's tables and this application's, and nothing else.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SHELL_TABLES = {
    "agent_runs",
    "agent_steps",
    "callback_tokens",
    "cues",
    "telegram_messages",
    "ui_sessions",
}
WALLET_TABLES = {"categories", "entries", "ledger", "wallets"}

RUNNER = Path(__file__).resolve().parent / "standalone_run.py"


def test_the_example_runs_its_whole_path_with_safwa_unimportable(tmp_path):
    finished = subprocess.run(
        [sys.executable, str(RUNNER), str(tmp_path / "standalone.db")],
        capture_output=True,
        text=True,
        cwd=str(RUNNER.parents[2]),
    )

    assert finished.returncode == 0, finished.stdout + finished.stderr
    result = json.loads(finished.stdout.strip().splitlines()[-1])
    assert result["safwa_imported"] is False
    assert result["entries"] == 1
    assert "Lunch is on the Cash wallet." in result["answer"]
    assert set(result["tables"]) == SHELL_TABLES | WALLET_TABLES
