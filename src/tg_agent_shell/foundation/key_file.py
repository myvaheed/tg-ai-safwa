"""The database key, locked with a passphrase, in a file beside the database.

The key is 32 random bytes; the key file holds it encrypted with AES-256-GCM under a key
derived from the passphrase with scrypt. Each try at a passphrase costs about 128 MB and a
fraction of a second, which is what stands between a stolen backup and a guessed passphrase.
The format version and the scrypt parameters are authenticated with the key, so a file edited
to weaken them no longer unlocks. The passphrase itself is the application's to ask for.

A database and its key file are a pair, and a pair is only ever replaced by a pair. Windows
cannot swap two files in one step, so the new key file, published whole beside the live one,
is the moment a replacement happens: before it the live pair is untouched, and after it
the swap is completed, however often it is interrupted.

Only one process at a time holds a database (`hold`): the application for as long as it runs,
a restore or a passphrase change for as long as it takes. A replacement is made only by the
holder, so it never swaps the files under a running application, and whoever takes the hold
next completes one that was stopped part way.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .database import KEY_BYTES, DatabaseFile

if sys.platform == "win32":
    import msvcrt

    # A Windows lock refuses every read of the bytes it covers, so it covers one far past the
    # end of the empty lock file, as SQLite's own locks do, and copying the folder still works.
    _LOCKED_BYTE = 2**30

    def _lock(descriptor: int) -> None:
        os.lseek(descriptor, _LOCKED_BYTE, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)

    def _unlock(descriptor: int) -> None:
        os.lseek(descriptor, _LOCKED_BYTE, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


KEY_FILE_FORMAT = 1
MIN_PASSPHRASE_CHARS = 16
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
NONCE_BYTES = 12


class KeyFileError(Exception):
    """The key file was not read, written or unlocked; the message says which and why."""


class WrongPassphrase(KeyFileError):
    """The passphrase does not unlock the key file."""


class DatabaseBusy(Exception):
    """Another process holds the database: the application runs, or a replacement does."""


def key_path(database: Path) -> Path:
    """Where a database's key file lives: beside it, under the same name."""
    return database.with_suffix(".key")


def new_key() -> bytes:
    return secrets.token_bytes(KEY_BYTES)


def _passphrase_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    # The same words typed on another keyboard or console may arrive composed differently.
    words = unicodedata.normalize("NFC", passphrase).encode("utf-8")
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(words)


def _header_bytes(header: dict[str, object]) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


def lock_key(key: bytes, passphrase: str) -> bytes:
    """The key file's contents: `key` encrypted under `passphrase`."""
    if len(key) != KEY_BYTES:
        raise ValueError(f"A database key is {KEY_BYTES} bytes")
    if len(passphrase) < MIN_PASSPHRASE_CHARS:
        raise KeyFileError(f"A passphrase is at least {MIN_PASSPHRASE_CHARS} characters")
    salt = os.urandom(SALT_BYTES)
    header: dict[str, object] = {
        "format": KEY_FILE_FORMAT,
        "kdf": "scrypt",
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
    }
    nonce = os.urandom(NONCE_BYTES)
    locking = _passphrase_key(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    sealed = AESGCM(locking).encrypt(nonce, key, _header_bytes(header))
    document = {
        **header,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "key": base64.b64encode(sealed).decode("ascii"),
    }
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def unlock_key(content: bytes, passphrase: str) -> bytes:
    """The key inside a key file's contents."""
    try:
        document = json.loads(content)
        header = {name: document[name] for name in ("format", "kdf", "n", "r", "p", "salt")}
        if (header["format"], header["kdf"]) != (KEY_FILE_FORMAT, "scrypt"):
            raise KeyFileError(f"This key file has a format this version cannot read: {header}")
        nonce = base64.b64decode(document["nonce"])
        sealed = base64.b64decode(document["key"])
        salt = base64.b64decode(header["salt"])
        locking = _passphrase_key(passphrase, salt, header["n"], header["r"], header["p"])
    except (ValueError, KeyError, TypeError) as error:
        raise KeyFileError("This is not a key file") from error
    try:
        return AESGCM(locking).decrypt(nonce, sealed, _header_bytes(header))
    except InvalidTag as error:
        raise WrongPassphrase("The passphrase does not unlock the key file") from error


def _write_whole(path: Path, content: bytes) -> None:
    """Write `path` so that it holds all of `content` or is not replaced at all."""
    partial = path.with_name(f"{path.name}.partial")
    with partial.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def _staged(path: Path) -> Path:
    return path.with_name(f"{path.name}.new")


def remove_database_files(database: Path) -> None:
    """A database file and the WAL and shared-memory files SQLite keeps beside it."""
    for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
        path.unlink(missing_ok=True)


def _finish_replacement(database: Path) -> None:
    """Complete a replacement of the pair that was stopped part way, or drop one never begun."""
    key = key_path(database)
    staged_database, staged_key = _staged(database), _staged(key)
    if not staged_key.exists():
        remove_database_files(staged_database)
        return
    if staged_database.exists():
        # The old database's WAL would otherwise be replayed against the new file.
        for sidecar in (Path(f"{database}-wal"), Path(f"{database}-shm")):
            sidecar.unlink(missing_ok=True)
        os.replace(staged_database, database)
    os.replace(staged_key, key)


def lock_path(database: Path) -> Path:
    return database.with_name(f"{database.name}.lock")


_held: set[Path] = set()


@contextmanager
def hold(database: Path) -> Iterator[None]:
    """Keep the database to this process until the block ends; `DatabaseBusy` if another has it.

    The operating system drops the lock when the process ends, however it ends, so a crash
    leaves nothing to clear. A hold inside a hold of the same process is that same hold.
    Taking it completes a replacement that was stopped part way.
    """
    path = lock_path(database).resolve()
    if path in _held:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        try:
            _lock(descriptor)
        except OSError as error:
            raise DatabaseBusy(f"{database.name} is in use by another process") from error
        _held.add(path)
        try:
            _finish_replacement(database)
            yield
        finally:
            _held.discard(path)
            _unlock(descriptor)
    finally:
        os.close(descriptor)


def finish_replacement(database: Path) -> None:
    """Complete a replacement that was stopped part way, unless another process holds the database.

    That process completed any such replacement when it took the hold, or is making one now.
    """
    try:
        with hold(database):
            pass
    except DatabaseBusy:
        return


def replace_pair(database: Path, replacement: Path, key_content: bytes) -> None:
    """Put `replacement` and the key file holding `key_content` in place of the live pair.

    `replacement` is a closed database on the same disk as `database`.
    """
    with hold(database):
        os.replace(replacement, _staged(database))
        _write_whole(_staged(key_path(database)), key_content)
        _finish_replacement(database)


def read_key_file(database: Path, passphrase: str) -> bytes:
    try:
        content = key_path(database).read_bytes()
    except FileNotFoundError as error:
        raise KeyFileError(f"There is no key file at {key_path(database)}") from error
    return unlock_key(content, passphrase)


def create_key(database: Path, passphrase: str) -> None:
    """Make the key a new database will be encrypted with; the database is made on first open."""
    with hold(database):
        key = key_path(database)
        if database.exists() or key.exists():
            raise KeyFileError(f"{database} or {key} already exists; a new key is for a new database")
        _write_whole(key, lock_key(new_key(), passphrase))


def unlock_database(database: Path, passphrase: str) -> DatabaseFile:
    """The database with its key, proven to open it when it exists already.

    A key file that unlocks but does not open the database belongs to another one, and
    `DatabaseRefused` says so.
    """
    finish_replacement(database)
    file = DatabaseFile(database, read_key_file(database, passphrase))
    if database.exists():
        file.connect().close()
    return file
