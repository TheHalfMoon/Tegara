"""Secure-path gate for local writable SQLite position journals.

The sqlite3 Python API opens paths, not caller-pinned descriptors. Therefore
on-disk journals require parent directories inaccessible to another uid.
A malicious process running as the same uid or root is outside this boundary.
"""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote


def _canonical_path(location: str) -> Path:
    path = Path(location).expanduser().absolute()
    if ".." in path.parts or len(path.parts) < 2:
        raise ValueError("SQLite database path has unsafe components")
    # Only macOS root-owned /var and /tmp system aliases may be resolved.
    if path.parts[1] in ("var", "tmp"):
        alias = Path("/") / path.parts[1]
        target = Path("/private") / path.parts[1]
        info = os.lstat(alias)
        if (stat.S_ISLNK(info.st_mode) and info.st_uid == 0
                and alias.resolve() == target):
            path = target.joinpath(*path.parts[2:])
    return path


def _trusted_directory(fd: int, *, final: bool) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()):
        raise ValueError("SQLite directory owner is untrusted")
    if final and (info.st_uid != os.geteuid()
                  or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("SQLite immediate parent must be owner-only (0700)")
    if stat.S_IMODE(info.st_mode) & 0o022:
        # Root-owned sticky /private/tmp may contain a private directory.
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if final or not sticky_root:
            raise ValueError("SQLite directory is group/world writable")


def _check_identity(fd: int, parent_fd: int, path: Path) -> None:
    pinned = os.fstat(fd)
    try:
        by_parent = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        by_path = os.stat(path, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError("SQLite database changed during open") from exc
    for item in (pinned, by_parent, by_path):
        if (not stat.S_ISREG(item.st_mode)
                or item.st_uid != os.geteuid() or item.st_nlink != 1):
            raise ValueError("SQLite database must be a private regular file")
    identity = (pinned.st_dev, pinned.st_ino)
    if any((item.st_dev, item.st_ino) != identity
           for item in (by_parent, by_path)):
        raise ValueError("SQLite database changed during open")


_SQLITE_MAGIC = b"SQLite format 3\0"
_LEGACY_TABLE_COLUMNS = {
    "positions": {
        "id", "symbol", "right_type", "strike", "expiry", "multiplier",
        "entry_price", "initial_qty", "open_qty", "entry_fee", "exit_fee",
        "opened_at",
    },
    "fills": {"position_id", "fill_id", "qty", "price", "exit_fee", "at", "net"},
    "monitor": {"position_id", "armed", "last_quote_at"},
    "outbox": {
        "id", "position_id", "quote_at", "bid", "net", "target", "state",
        "created_at",
    },
}


def _verify_existing_ledger(fd: int, parent_fd: int, path: Path) -> None:
    """Refuse unrelated disk files before chmod or writable SQLite initialization.

    Existing old-format Tegara databases need the stable core tables; later
    columns are intentionally optional so the normal migrations still work.
    Read-only immutable preflight must not create SQLite journal sidecars.
    """
    size = os.fstat(fd).st_size
    if size == 0:
        raise ValueError("existing empty file is not a verified Tegara journal")
    if size < 100 or os.pread(fd, 16, 0) != _SQLITE_MAGIC:
        raise ValueError("existing file is not a Tegara SQLite journal")
    _check_identity(fd, parent_fd, path)
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as preview:
            tables = {
                row[0] for row in preview.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not _LEGACY_TABLE_COLUMNS.keys() <= tables:
                raise ValueError("existing SQLite database is not a Tegara journal")
            for table, required in _LEGACY_TABLE_COLUMNS.items():
                columns = {
                    row[1] for row in preview.execute(f"PRAGMA table_info({table})")
                }
                if not required <= columns:
                    raise ValueError("existing SQLite schema is not a Tegara journal")
    except sqlite3.Error as exc:
        raise ValueError("existing SQLite database cannot be safely qualified") from exc
    _check_identity(fd, parent_fd, path)


def open_private_sqlite(location: str) -> sqlite3.Connection:
    """Open and revalidate a private SQLite file before any schema writes."""
    if not all(hasattr(os, key) for key in ("O_NOFOLLOW", "O_DIRECTORY")):
        raise ValueError("Atomic no-follow SQLite opening unavailable")
    path = _canonical_path(location)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = (os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
                  | getattr(os, "O_NONBLOCK", 0))
    parent_fd = os.open("/", directory_flags)
    file_fd = None
    connection = None
    try:
        for component in path.parts[1:-1]:
            _trusted_directory(parent_fd, final=False)
            try:
                child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                try:
                    child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
                except OSError as exc:
                    raise ValueError("SQLite directory symlinks are refused") from exc
            except OSError as exc:
                raise ValueError("SQLite directory symlinks are refused") from exc
            os.close(parent_fd)
            parent_fd = child_fd
        _trusted_directory(parent_fd, final=True)
        existing = False
        try:
            file_fd = os.open(path.name, file_flags | os.O_CREAT | os.O_EXCL,
                              0o600, dir_fd=parent_fd)
        except FileExistsError:
            existing = True
            try:
                file_fd = os.open(path.name, file_flags, dir_fd=parent_fd)
            except OSError as exc:
                raise ValueError("SQLite database symlinks are refused") from exc
        _check_identity(file_fd, parent_fd, path)
        if existing:
            _verify_existing_ledger(file_fd, parent_fd, path)
        os.fchmod(file_fd, 0o600)
        _check_identity(file_fd, parent_fd, path)
        # mode=rw means SQLite cannot silently create a different file.
        uri = "file:" + quote(str(path), safe="/") + "?mode=rw"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=10)
        _check_identity(file_fd, parent_fd, path)
        result = connection
        connection = None
        return result
    finally:
        if connection is not None:
            connection.close()
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)
