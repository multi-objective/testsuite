#!/usr/bin/env python3
import os
import subprocess
import sys
import glob
import time
import tempfile
from itertools import zip_longest
from joblib import Parallel, delayed
from rich import print as print_rich

import re
import difflib
import lzma

# debug = True will use the command-line diff (or xzdiff) to check the results of difflib.
debug = False

_RE_COMBINE_WHITESPACE = re.compile(r"\s+")

ASAN_OPTIONS = os.getenv("ASAN_OPTIONS", "")
UBSAN_OPTIONS = os.getenv("UBSAN_OPTIONS", "")


def normalize_file_lines(file_path):
    """Yield normalized lines from a file, supporting both regular and XZ-compressed files."""
    open_func = lzma.open if file_path.endswith(".xz") else open
    with open_func(file_path, "rt", encoding="utf-8") as f:
        for line in f:
            line = _RE_COMBINE_WHITESPACE.sub(
                " ", line
            ).strip()  # Normalize whitespace, and tabs
            if line:  # Ignore blank lines
                yield line.lower()  # Normalize case


# This function is adapted from https://github.com/python/cpython/blob/main/Lib/doctest.py
# Released to the public domain 16-Jan-2001, by Tim Peters (tim@python.org).
def ellipsis_match(want, got):
    """ "Compares ``want`` to ``got`` ignoring differences where ``...`` appears in ``want``."""
    ELLIPSIS_MARKER = "..."
    if ELLIPSIS_MARKER not in want:
        return want == got

    # Find "the real" strings.
    ws = want.split(ELLIPSIS_MARKER)
    assert len(ws) >= 2

    # Deal with exact matches possibly needed at one or both ends.
    startpos, endpos = 0, len(got)
    w = ws[0]
    if w:  # starts with exact match
        if got.startswith(w):
            startpos = len(w)
            del ws[0]
        else:
            return False
    w = ws[-1]
    if w:  # ends with exact match
        if got.endswith(w):
            endpos -= len(w)
            del ws[-1]
        else:
            return False

    if startpos > endpos:
        # Exact end matches required more characters than we have, as in
        # _ellipsis_match('aa...aa', 'aaa')
        return False

    # For the rest, we only need to find the leftmost non-overlapping
    # match for each piece.  If there's no overall match that way alone,
    # there's no overall match period.
    for w in ws:
        # w may be '' at times, if there are consecutive ellipses, or
        # due to an ellipsis at the start or end of `want`.  That's OK.
        # Search for an empty string succeeds, and doesn't change startpos.
        startpos = got.find(w, startpos, endpos)
        if startpos < 0:
            return False
        startpos += len(w)

    return True


def generate_unified_diff(file1, file2):
    """Generate a unified diff between two files with normalization."""

    lines1 = list(normalize_file_lines(file1))
    lines2 = list(normalize_file_lines(file2))
    equal = True

    for want, got in zip_longest(lines1, lines2, fillvalue=""):
        # We do not allow the ellipsis to span multiple lines.
        if not ellipsis_match(want=want, got=got):
            equal = False
            break

    if equal:
        return ""

    diff = difflib.unified_diff(
        lines1, lines2, fromfile=file1, tofile=file2, lineterm=""
    )
    return "\n".join(diff)


def truncate_lines(output: str, max_lines: int) -> str:
    lines = output.splitlines()
    if len(lines) <= max_lines:
        return output  # Return unmodified
    return "\n".join(lines[:max_lines] + ["...truncated"])


def runcmd(command, cwd=None, env=None, outfile=subprocess.DEVNULL):
    # Merge with os.environ to preserve PATH and other environment variables
    full_env = os.environ.copy()
    if env is not None:
        full_env.update(env)
    full_env["LC_ALL"] = "C"  # To avoid problems with sorting.
    full_env["ASAN_OPTIONS"] = ASAN_OPTIONS
    full_env["UBSAN_OPTIONS"] = UBSAN_OPTIONS
    stdout = outfile if outfile == subprocess.DEVNULL else subprocess.PIPE
    result = subprocess.run(
        command,
        shell=True,
        env=full_env,
        cwd=cwd,
        stdout=stdout,
        stderr=subprocess.STDOUT,
    )

    if stdout == subprocess.PIPE:
        open_func = lzma.open if outfile.endswith(".xz") else open
        with open_func(outfile, "wb") as fh:
            fh.write(result.stdout)

    return result.returncode


def is_exe(fpath):
    fpath = os.path.expanduser(fpath)
    return (
        os.path.isfile(fpath)
        and os.access(fpath, os.X_OK)
        and os.path.getsize(fpath) > 0
    )


def run_test(test, program):
    print("{:<60}".format("Running " + test + " :"), end=" ")
    testdirname = os.path.dirname(test)
    if testdirname == "":
        testdirname = None
    out_ext = ".out"
    expfile = test.replace(".test", ".exp")
    if not os.access(expfile, os.R_OK) and os.access(expfile + ".xz", os.R_OK):
        expfile += ".xz"
        out_ext += ".xz"

    testbasename = os.path.basename(test)
    fh, outfile = tempfile.mkstemp(suffix="_" + testbasename.replace(".test", out_ext))
    os.close(fh)
    start_time = time.time()
    # FIXME: How can we avoid using '.' to read the test?
    # Using 'source' only works in bash.
    runcmd(
        f". ./{testbasename}",
        cwd=testdirname,
        env=dict(PROGRAM=program, TESTNAME=testbasename.replace(".test", "")),
        outfile=outfile,
    )
    elapsed_time = time.time() - start_time

    diff_output = generate_unified_diff(expfile, outfile)
    if len(diff_output) == 0:
        print_rich(f"passed[green]✓[/] {elapsed_time:6.2f}")
        os.remove(outfile)
        return True
    else:
        print_rich(f"[bold red]FAILED![/] {elapsed_time:6.2f}")
        print(truncate_lines(diff_output, max_lines=20))
        if debug:
            diff = "xzdiff" if expfile.endswith(".xz") else "diff"
            print(subprocess.getoutput(f"{diff} -uiEBw -- {expfile} {outfile}"))
            assert runcmd(f"{diff} -iEBwq -- {expfile} {outfile}") != 0, diff_output
        return False


def main():
    if len(sys.argv) < 2:
        print("usage:", sys.argv[0], "PROGRAM [TESTS]")
        print("\t for example:", sys.argv[0], "../bin/hv")
        sys.exit(1)

    program = os.path.realpath(os.path.expanduser(sys.argv[1]))
    if not is_exe(program):
        print(f"error: '{program}' not found or not executable!")
        sys.exit(1)

    tests = (
        sorted(glob.glob("**/*.test", recursive=True))
        if len(sys.argv) == 2
        else sys.argv[2:]
    )
    for test in tests:
        if not test.endswith(".test"):
            print(test, "is not a test file")
            sys.exit(1)
        if not os.path.isfile(test):
            print(test, "not found or not readable")
            sys.exit(1)

    ntotal = len(tests)
    elapsed_time = time.time()
    ok = Parallel(n_jobs=-2)(delayed(run_test)(test, program=program) for test in tests)
    elapsed_time = time.time() - elapsed_time
    npassed = sum(ok)
    nfailed = ntotal - npassed
    print(f"""
===== regression test summary =====
 # of total tests : {ntotal:5d}
 # of passed tests: {npassed:5d}
 # of failed tests: {nfailed:5d}
 #     total time : {elapsed_time:8.2f}
    """)
    exitcode = 1 if nfailed > 0 else 0
    sys.exit(exitcode)


if __name__ == "__main__":
    main()
