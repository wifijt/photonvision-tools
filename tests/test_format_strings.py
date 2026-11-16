#!/usr/bin/env python3
#
# Copyright (C) 2026  wifijt
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.  This program is distributed WITHOUT ANY WARRANTY; see the GNU
# General Public License at <https://www.gnu.org/licenses/> for details.
#
"""Every %-format string must match its argument count.

Written because preflight.require_nt shipped with three placeholders and two
arguments. Nothing caught it, for a reason worth stating: the broken line was
inside an error path. The code ran for weeks, the tests passed, and the bug was
waiting in the one branch whose job was to explain a failure - so the failure
it explained became a TypeError traceback instead.

A mismatch here is invisible until the branch runs, and error branches are
exactly the ones that do not run in testing. So check them statically: this
walks every .py file with ast and counts, never importing anything (several
tools parse arguments at import time).

    python3 tests/test_format_strings.py

Exits non-zero and names every offender, so it works as a pre-commit hook.

LIMITS, stated honestly: it only checks `"..." % (a, b)` where the format is a
literal and the right side is a literal tuple. A format built from a variable,
or a single non-tuple argument, is not checked - a single argument cannot have
the arity bug this exists to catch, and a computed format string cannot be
counted without running it.
"""
import ast
import os
import sys

SKIP_DIRS = {".git", "__pycache__", "dashboards", ".venv", "venv"}

#: Conversion type characters that END a %-spec. 'b' is included for bytes
#: formats; '*' inside a spec consumes an extra argument and is counted too.
TERMINATORS = "diouxXeEfFgGcrsab"


def conversions(fmt):
    """How many arguments `fmt` consumes. Handles %% and the * width."""
    count = 0
    i = 0
    while i < len(fmt):
        if fmt[i] != "%":
            i += 1
            continue
        if i + 1 < len(fmt) and fmt[i + 1] == "%":
            i += 2                       # a literal percent sign
            continue
        j = i + 1
        while j < len(fmt) and fmt[j] not in TERMINATORS:
            if fmt[j] == "*":
                count += 1               # width or precision from an argument
            j += 1
        count += 1
        i = j + 1
    return count


def offenders(root="."):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                tree = ast.parse(open(path).read())
            except (SyntaxError, UnicodeDecodeError) as e:
                out.append((path, 0, "could not parse: %s" % e))
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.BinOp)
                        and isinstance(node.op, ast.Mod)
                        and isinstance(node.left, ast.Constant)
                        and isinstance(node.left.value, str)
                        and isinstance(node.right, ast.Tuple)):
                    continue
                want = conversions(node.left.value)
                got = len(node.right.elts)
                if want != got:
                    first = node.left.value.splitlines()[0][:60]
                    out.append((path, node.lineno,
                                "%d placeholders, %d arguments: %r"
                                % (want, got, first)))
    return out


def selftest():
    """The counter itself needs checking before it is trusted."""
    cases = [("%s", 1), ("%s %s", 2), ("100%% of %s", 1), ("%d%%", 1),
             ("%-28s :%-5d %s", 3), ("%.2f", 1), ("%*d", 2), ("no specs", 0),
             ("%(name)s", 1), ("%s\n%g", 2)]
    bad = [(f, n, conversions(f)) for f, n in cases if conversions(f) != n]
    for f, want, got in bad:
        print("  SELFTEST FAIL %r: counted %d, expected %d" % (f, got, want))
    return not bad


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    if not selftest():
        sys.exit("the conversion counter is wrong; fix it before trusting a pass")
    print("conversion counter self-test ok")
    found = offenders(root)
    for path, line, why in found:
        print("%s:%d  %s" % (os.path.relpath(path, root), line, why))
    if found:
        sys.exit("\n%d format string(s) do not match their arguments" % len(found))
    print("every %-format string matches its argument count")
