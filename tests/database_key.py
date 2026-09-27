"""The fixed key every test database is encrypted with, so no test ever asks for a passphrase,
and another process to hold a database the way a running application does."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine

from tg_agent_shell.foundation.database import DatabaseFile, sync_engine

TEST_KEY = bytes(range(32))


def keyed(path: Path) -> DatabaseFile:
    """The test database at `path`, opened with the test key."""
    return DatabaseFile(path, TEST_KEY)


def keyed_engine(path: Path) -> Engine:
    """A synchronous engine on the test database at `path`, for a test that builds or inspects it."""
    return sync_engine(keyed(path))


_HOLDER = """
import sys
from pathlib import Path

from tg_agent_shell.foundation.key_file import hold

with hold(Path(sys.argv[1])):
    print("held", flush=True)
    sys.stdin.read()
"""


@contextmanager
def held_elsewhere(database: Path) -> Iterator[None]:
    """Another process holds `database` for the block, and is killed at its end, as a crash would."""
    with subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(database)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            yield
        finally:
            holder.kill()
