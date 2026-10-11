#!/usr/bin/env python3
"""Portable replacement for ``sort`` (LC_ALL=C order) supporting
``-b/--ignore-leading-blanks`` and ``-s/--stable``."""
import sys


def main(args):
    blanks = stable = False
    files = []
    for a in args:
        if a == "--ignore-leading-blanks":
            blanks = True
        elif a == "--stable":
            stable = True
        elif a.startswith("-") and not a.startswith("--") and len(a) > 1:
            for c in a[1:]:
                if c == "b":
                    blanks = True
                elif c == "s":
                    stable = True
                else:
                    sys.exit(f"sort.py: unsupported option -{c}")
        else:
            files.append(a)

    data = b""
    if not files:
        files = ["-"]
    for name in files:
        if name == "-":
            data += sys.stdin.buffer.read()
        else:
            with open(name, "rb") as f:
                data += f.read()

    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()

    def key(line):
        k = line.rstrip(b"\r")
        return k.lstrip(b" \t") if blanks else k

    if stable:
        lines.sort(key=key)
    else:
        # Like GNU sort, fall back to comparing the whole line.
        lines.sort(key=lambda x: (key(x), x.rstrip(b"\r")))
    out = sys.stdout.buffer
    out.write(b"".join(line + b"\n" for line in lines))
    out.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
