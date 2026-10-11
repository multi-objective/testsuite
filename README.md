moocore testsuite
=================

[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/multi-objective/testsuite/main.svg)](https://results.pre-commit.ci/latest/github/multi-objective/testsuite/main)
[![Testsuite status][testsuite-badge]][testsuite-link]

This is a testsuite for [`moocore`](https://github.com/multi-objective/moocore).

[testsuite-badge]: https://github.com/multi-objective/testsuite/actions/workflows/moocore.yml/badge.svg
[testsuite-link]: https://github.com/multi-objective/testsuite/actions/workflows/moocore.yml

Running the testsuite
---------------------

```bash
pip install -r requirements.txt
cd hv && ../regtest.py ../../moocore/bin/hv
```

`regtest.py PROGRAM [TESTS]` runs the `*.test` files in the current directory (recursively) using `PROGRAM`.
The `*.test` files do not call platform-specific utilities directly but use the environment variables
`$XZCAT`, `$SORT`, `$CAT_SQUEEZE` and `$MKTEMP`. On Linux/macOS they default to `xzcat`, `sort`, `cat -s` and `mktemp`
and can be overridden from the environment.

Windows
-------

The testsuite runs on Windows with the executables of `moocore` built with MSVC.
`regtest.py` only needs Python (and optionally `joblib`, otherwise tests run sequentially); no Unix utilities are required.
On Windows, `regtest.py`:

 * runs the `*.test` files with a built-in minimal interpreter (variables, `|`, `<`, `>`, `echo`, `rm`) instead of a shell, and
 * uses the portable Python scripts in [`tools/`](tools) for `$XZCAT`, `$SORT`, `$CAT_SQUEEZE` and `$MKTEMP`.

moocore's makefile adds the `.exe` extension to the executables, and `regtest.py` appends it if missing, so these are equivalent:

```bat
rem From a Visual Studio developer prompt (or after ilammy/msvc-dev-cmd)
make -C moocore\c all CC=cl
cd testsuite\hv
python ..\regtest.py ..\..\moocore\bin\hv
python ..\regtest.py ..\..\moocore\bin\hv.exe
```

Set `REGTEST_PORTABLE=1` to use this Windows code path on Linux/macOS (useful for debugging).
