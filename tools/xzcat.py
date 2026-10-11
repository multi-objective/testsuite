#!/usr/bin/env python3
"""Portable replacement for ``xzcat``: decompress FILEs (or stdin) to stdout."""
import lzma
import shutil
import sys


def main(files):
    out = sys.stdout.buffer
    if not files:
        files = ["-"]
    for name in files:
        if name == "-":
            shutil.copyfileobj(lzma.LZMAFile(sys.stdin.buffer), out)
        else:
            with lzma.open(name, "rb") as f:
                shutil.copyfileobj(f, out)
    out.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
