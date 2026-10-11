#!/usr/bin/env python3
import os
import subprocess
import sys
import glob
import time
import tempfile
import shlex
from itertools import zip_longest
from pathlib import Path
from rich import print as print_rich

try:
    from joblib import Parallel, delayed
except ImportError:  # joblib is optional: fall back to sequential execution.
    Parallel = delayed = None

import re
import difflib
import lzma

# debug = True will use the command-line diff (or xzdiff) to check the results of difflib.
debug = False

IS_WINDOWS = sys.platform == "win32"
# Set REGTEST_PORTABLE=1 to use the Windows code path on other platforms.
USE_PORTABLE = IS_WINDOWS or os.getenv("REGTEST_PORTABLE") == "1"

# Make sure that Python subprocesses and joblib workers use UTF-8.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def setup_utf8_output():
    """Avoid console-encoding crashes when printing unicode symbols."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


setup_utf8_output()

TOOLS_DIR = Path(__file__).resolve().parent / "tools"


def _python_tool(script):
    return shlex.join([Path(sys.executable).as_posix(), (TOOLS_DIR / script).as_posix()])


# Commands used by the *.test files. Unix defaults are the standard utilities,
# on Windows they are portable Python scripts from tools/. They can be
# overridden by setting the corresponding environment variable.
if USE_PORTABLE:
    _TOOL_DEFAULTS = {
        "XZCAT": _python_tool("xzcat.py"),
        "SORT": _python_tool("sort.py"),
        "CAT_SQUEEZE": _python_tool("cat_squeeze.py"),
        "MKTEMP": _python_tool("mktemp.py"),
    }
else:
    _TOOL_DEFAULTS = {
        "XZCAT": "xzcat",
        "SORT": "sort",
        "CAT_SQUEEZE": "cat -s",
        "MKTEMP": "mktemp",
    }
TOOL_ENV = {k: os.environ.get(k, v) for k, v in _TOOL_DEFAULTS.items()}

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


_RE_VAR = re.compile(r"\$(?:(\w+)|\{(\w+)\})")
_RE_ASSIGN = re.compile(r"^(\w+)=(.*)$")
_RE_SUBST = re.compile(r"^\$\((.*)\)$")


def _expand(word, variables):
    return _RE_VAR.sub(lambda m: variables.get(m.group(1) or m.group(2), ""), word)


def _split_line(line):
    lex = shlex.shlex(line, posix=True, punctuation_chars="|<>")
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _run_pipeline(line, variables, cwd, env):
    """Run one simple pipeline of a test file (a tiny POSIX shell subset).

    Supports variable expansion, '|', '<', '>', and the builtins echo and rm.
    Returns the combined stdout/stderr of the pipeline as bytes.
    """
    stages = [[]]
    stdin_file = stdout_file = None
    tokens = _split_line(line)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "|":
            stages.append([])
        elif tok in ("<", ">"):
            i += 1
            target = _expand(tokens[i], variables)
            if tok == "<":
                stdin_file = target
            else:
                stdout_file = target
        else:
            stages[-1].append(tok)
        i += 1

    argvs = []
    for words in stages:
        argv = []
        for j, w in enumerate(words):
            expanded = _expand(w, variables)
            if j == 0 and w.startswith("$") and expanded.strip():
                argv.extend(shlex.split(expanded))  # command stored in a variable
            else:
                argv.append(expanded)
        argvs.append(argv)

    if len(argvs) == 1 and argvs[0] and argvs[0][0] in ("echo", "rm"):
        argv = argvs[0]
        if argv[0] == "echo":
            out = (" ".join(argv[1:]) + "\n").encode("utf-8")
            if stdout_file:
                Path(cwd or ".", stdout_file).write_bytes(out)
                return b""
            return out
        for name in (a for a in argv[1:] if not a.startswith("-")):
            try:
                os.remove(os.path.join(cwd or ".", name))
            except OSError:
                pass
        return b""

    opened = []
    procs = []
    errs = []
    try:
        prev_out = None
        if stdin_file:
            prev_out = open(os.path.join(cwd or ".", stdin_file), "rb")
            opened.append(prev_out)
        for k, argv in enumerate(argvs):
            last = k == len(argvs) - 1
            if last and stdout_file:
                stdout = open(os.path.join(cwd or ".", stdout_file), "wb")
                opened.append(stdout)
            else:
                stdout = subprocess.PIPE
            if last:
                stderr = subprocess.STDOUT
            else:
                stderr = tempfile.TemporaryFile()
                errs.append(stderr)
            p = subprocess.Popen(
                argv, stdin=prev_out, stdout=stdout, stderr=stderr, cwd=cwd, env=env
            )
            if k > 0:
                procs[-1].stdout.close()
            procs.append(p)
            prev_out = p.stdout
        out = procs[-1].communicate()[0] or b""
        for p in procs[:-1]:
            p.wait()
        extra = b""
        for e in errs:
            e.seek(0)
            extra += e.read()
            e.close()
        return extra + out
    finally:
        for f in opened:
            f.close()


def run_script_portable(script, cwd, env):
    """Execute a *.test file without a POSIX shell (used on Windows)."""
    variables = {k: env[k] for k in env}
    output = b""
    with open(os.path.join(cwd or ".", script), "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _RE_ASSIGN.match(line)
        if m and " " not in m.group(1):
            name, value = m.groups()
            sub = _RE_SUBST.match(value)
            if sub:
                value = _run_pipeline(sub.group(1), variables, cwd, env)
                value = value.decode("utf-8").rstrip("\r\n")
            else:
                value = "".join(_split_line(value)) if value else ""
                value = _expand(value, variables)
            variables[name] = value
            continue
        output += _run_pipeline(line, variables, cwd, env)
    return output


def runcmd(command, cwd=None, env=None, outfile=subprocess.DEVNULL):
    # Merge with os.environ to preserve PATH and other environment variables
    full_env = os.environ.copy()
    if env is not None:
        full_env.update(env)
    full_env["LC_ALL"] = "C"  # To avoid problems with sorting.
    full_env["ASAN_OPTIONS"] = ASAN_OPTIONS
    full_env["UBSAN_OPTIONS"] = UBSAN_OPTIONS
    full_env.update(TOOL_ENV)
    full_env["PYTHONUTF8"] = "1"
    full_env["PYTHONIOENCODING"] = "utf-8"
    if USE_PORTABLE and command.startswith(". ./"):
        data = run_script_portable(command[4:], cwd, full_env)
        if outfile != subprocess.DEVNULL:
            open_func = lzma.open if outfile.endswith(".xz") else open
            with open_func(outfile, "wb") as fh:
                fh.write(data)
        return 0
    stdout = outfile if outfile == subprocess.DEVNULL else subprocess.PIPE
    result = subprocess.run(
        command,
        shell=True,
        env=full_env,
        check=False,
        cwd=cwd,
        stdout=stdout,
        stderr=subprocess.STDOUT,
    )

    if stdout == subprocess.PIPE:
        open_func = lzma.open if outfile.endswith(".xz") else open
        with open_func(outfile, "wb") as fh:
            fh.write(result.stdout)

    return result.returncode


def find_exe(fpath):
    """Return the path of the executable, adding '.exe' on Windows if needed.

    moocore's makefile appends '.exe' to the executables it builds.
    """
    fpath = os.path.realpath(os.path.expanduser(fpath))
    if IS_WINDOWS and not fpath.lower().endswith(".exe"):
        if os.path.isfile(fpath + ".exe") or not os.path.isfile(fpath):
            fpath += ".exe"
    return fpath


def is_exe(fpath):
    fpath = os.path.expanduser(fpath)
    return (
        os.path.isfile(fpath)
        and os.access(fpath, os.X_OK)
        and os.path.getsize(fpath) > 0
    )


def run_test(test, program):
    setup_utf8_output()
    if USE_PORTABLE:
        program = Path(program).as_posix()
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
        env={"PROGRAM": program, "TESTNAME": testbasename.replace(".test", "")},
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

    program = find_exe(sys.argv[1])
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
    ok = None
    if Parallel is not None:
        try:
            ok = Parallel(n_jobs=-2)(
                delayed(run_test)(test, program=program) for test in tests
            )
        except Exception as e:  # e.g. worker processes cannot be spawned
            print(f"warning: parallel execution failed ({e!r}); running sequentially")
    if ok is None:
        ok = [run_test(test, program=program) for test in tests]
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
