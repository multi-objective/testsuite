#!/usr/bin/env python3
"""Portable replacement for ``cat --squeeze-blank`` (``cat -s``)."""
import sys


def main(files):
    out = sys.stdout.buffer
    previous_blank = False
    for name in files or ["-"]:
        if name == "-":
            f = sys.stdin.buffer
        else:
            f = open(name, "rb")
        for line in f:
            blank = line.rstrip(b"\r\n") == b""
            if blank and previous_blank:
                continue
            previous_blank = blank
            out.write(line)
        if f is not sys.stdin.buffer:
            f.close()
    out.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
