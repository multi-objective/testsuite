#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import glob
import time
import tempfile
from joblib import Parallel, delayed

import difflib
import lzma
import re

_RE_COMBINE_WHITESPACE = re.compile(r"\s+")

# FIXME: Replace "diff" with difflib.
def normalize_file_lines(file_path):
    """Yield normalized lines from a file, supporting both regular and XZ-compressed files."""
    open_func = lzma.open if file_path.endswith('.xz') else open
    with open_func(file_path, 'rt', encoding='utf-8') as f:
        for line in f:
            line = _RE_COMBINE_WHITESPACE.sub(" ", line).strip() # Normalize whitespace, and tabs
            if line:  # Ignore blank lines
                yield line.lower() # Normalize case

def generate_unified_diff(file1, file2):
    """Generate a unified diff between two files with normalization."""
    diff = difflib.unified_diff(
        list(normalize_file_lines(file1)),
        list(normalize_file_lines(file2)),
        fromfile=file1, 
        tofile=file2, 
        lineterm=''
    )
    return '\n'.join(diff)

def runcmd(command, cwd = None, env = {}, outfile = subprocess.DEVNULL):
    env['LC_ALL'] = "C" # To avoid problems with sorting.
    stdout = outfile if outfile == subprocess.DEVNULL else subprocess.PIPE
    result = subprocess.run(command, shell=True, env = env, cwd = cwd,
                            stdout = stdout, stderr = subprocess.STDOUT)

    if stdout == subprocess.PIPE:
        open_func = lzma.open if outfile.endswith('.xz') else open
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

def run_test(test, program, dir_out):
    testdirname = os.path.dirname(test)
    if testdirname == "":
        testdirname = None
    testbasename = os.path.basename(test)
    outfile = os.path.join(dir_out, testbasename.replace(".test", ".out"))
    expfile = test.replace(".test", ".exp")
    diff = "diff"
    if not os.access(expfile, os.R_OK) and os.access(expfile + ".xz", os.R_OK):
        expfile = expfile + ".xz"
        outfile = outfile + ".xz"
        diff = "xzdiff"

    print("{:<60}".format("Running " + test + " :"), end=" ")
    start_time = time.time()
    # FIXME: How can we avoid using '.' to read the test?
    # Using 'source' only works in bash.
    runcmd(f". ./{testbasename}", cwd = testdirname,
           env = dict(PROGRAM=program,
                      TESTNAME=testbasename.replace(".test", "")),
           outfile = outfile)
    elapsed_time = time.time() - start_time

    diff_output = generate_unified_diff(expfile, outfile)
    if len(diff_output) == 0:
        print(f"passed  {elapsed_time:6.2f}")
        assert runcmd(f"{diff} -iEBwq -- {expfile} {outfile}") == 0
        os.remove(outfile)
        return True
    else:
        print(f"FAILED! {elapsed_time:6.2f}")
        print(diff_output) # f"{diff} -uiEBw -- {expfile} {outfile}")
        #print(subprocess.getoutput(f"{diff} -uiEBw -- {expfile} {outfile}"))
        assert runcmd(f"{diff} -iEBwq -- {expfile} {outfile}") != 0, f"{generate_unified_diff(expfile, outfile)}"
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

    tests = sorted(glob.glob("*.test")) if len(sys.argv) == 2 else sys.argv[2:]
    for test in tests:
        if not test.endswith(".test"):
            print(test, "is not a test file")
            sys.exit(1)
        if not os.path.isfile(test):
            print(test, "not found or not readable")
            sys.exit(1)
            

    ntotal = len(tests)
    dir_out = tempfile.mkdtemp()
    ok = Parallel(n_jobs=-2)(
        delayed(run_test)(test, dir_out=dir_out, program=program) for test in tests
    )
    npassed = sum(ok)
    nfailed = ntotal - npassed
    if nfailed == 0:
        shutil.rmtree(dir_out)
    print("\n === regression test summary ===\n")
    print(f"# of total tests : {ntotal:5d}")
    print(f"# of passed tests: {npassed:5d}")
    print(f"# of failed tests: {nfailed:5d}\n")
    exitcode = 1 if nfailed > 0 else 0
    sys.exit(exitcode)


if __name__ == "__main__":
    main()
