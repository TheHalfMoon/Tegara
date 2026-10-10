"""Bounded, symlink-safe read-only CSV snapshots for offline research.

The pinned component walk protects the named file's path provenance from
intermediate symlinks, not against a malicious writer altering its bytes.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


def read_csv_snapshot(source: str | Path, limit: int) -> bytes:
    """Return one bounded byte snapshot without following directory symlinks."""
    if type(limit) is not int or limit <= 0:
        raise ValueError("CSV snapshot limit must be a positive integer")
    path = Path(source).expanduser()
    if ".." in path.parts:
        raise ValueError("CSV source cannot traverse parent components")
    path = path.absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("CSV must be an existing regular non-symlink file")

    # macOS provides fixed system /var and /tmp aliases to /private. Only
    # normalize those exact known aliases, not caller-specified symlinks.
    if len(path.parts) > 1 and path.parts[1] in ("var", "tmp"):
        alias = path.parts[1]
        canonical = Path("/private") / alias
        root_alias = Path("/") / alias
        if root_alias.is_symlink() and root_alias.resolve() == canonical:
            path = canonical.joinpath(*path.parts[2:])

    if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise ValueError("atomic no-follow CSV opening is unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_fd = os.open("/", directory_flags)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        file_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        with os.fdopen(file_fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ValueError("CSV must be an ordinary regular file")
            raw = f.read(limit + 1)
    finally:
        os.close(parent_fd)
    if len(raw) > limit:
        raise ValueError("CSV exceeds bounded local input size")
    return raw
