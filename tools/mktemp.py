#!/usr/bin/env python3
"""Portable replacement for ``mktemp [-u]``: print a temporary file name."""
import os
import sys
import tempfile
from pathlib import Path


def main(args):
    if args and args[0] not in ("-u", "--dry-run"):
        sys.exit(f"mktemp.py: unsupported option {args[0]}")
    if args:
        # Name only: do not create the file.
        path = Path(tempfile.gettempdir()) / ("tmp." + os.urandom(6).hex())
    else:
        fd, name = tempfile.mkstemp()
        os.close(fd)
        path = Path(name)
    print(path.as_posix())


if __name__ == "__main__":
    main(sys.argv[1:])
