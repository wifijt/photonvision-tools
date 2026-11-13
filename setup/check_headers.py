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
"""Every tool's header must list every option the tool accepts.

    python3 setup/check_headers.py              report which tools are missing what
    python3 setup/check_headers.py --write      generate the OPTIONS block in place

WHY. A docstring that explains the method but not the flags teaches you how the
thing works and not how to run it. `calibrate_mount` is the case that prompted
this: it carries a long and genuinely useful explanation of what "forward"
means, and never mentions that --motion exists - so the only way to find out
was --help or reading the parser. Someone recorded a second spin as a drive.

HOW IT STAYS TRUE. The block is generated from the argparse calls by reading
the file, never by importing it - several tools parse arguments at import time
and one of them opens a socket. Run with no arguments it only reports, which
makes it usable as a test.

OPTIONS
    --write     rewrite the OPTIONS block in every tool that needs one
    --quiet     only print the summary line
"""
import argparse
import ast
import os
import re
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = "OPTIONS"
SKIP = {"setup/check_headers.py"}


def _text(node):
    """A string literal, including implicit concatenation across lines."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _text(node.left), _text(node.right)
        return (a + b) if a is not None and b is not None else None
    return None


def options(path):
    """[(name, metavar, default, help)] straight from the add_argument calls."""
    tree = ast.parse(open(path).read())
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add_argument"):
            continue
        flags = [t for t in (_text(a) for a in node.args) if t]
        if not flags:
            continue
        kw = {k.arg: k.value for k in node.keywords}
        name = ", ".join(f for f in flags if f.startswith("-")) or flags[0]
        meta = _text(kw["metavar"]) if "metavar" in kw else None
        if isinstance(kw.get("metavar"), ast.Tuple):
            meta = " ".join(_text(e) or "?" for e in kw["metavar"].elts)
        choices = kw.get("choices")
        if meta is None and isinstance(choices, ast.List):
            meta = " | ".join(str(_text(e)) for e in choices.elts)
        default = None
        if "default" in kw and isinstance(kw["default"], ast.Constant):
            if kw["default"].value not in (None, False):
                default = str(kw["default"].value)
        help_ = _text(kw.get("help")) if "help" in kw else None
        out.append((name, meta, default, help_))
    return out


def block(opts, width=79):
    lines = [START]
    pad = max((len(n) + (len(m) + 1 if m else 0)) for n, m, _, _ in opts) + 4
    pad = min(pad, 28)
    for name, meta, default, help_ in opts:
        left = name + (" " + meta if meta else "")
        body = (help_ or "").strip()
        if default:
            body = (body + "  " if body else "") + "(default %s)" % default
        if not body:
            lines.append("    " + left)
            continue
        wrapped = textwrap.wrap(body, width=width - pad - 4) or [""]
        lines.append("    %-*s%s" % (pad, left, wrapped[0]))
        for w in wrapped[1:]:
            lines.append("    %-*s%s" % (pad, "", w))
    return "\n".join(lines)


def current(doc):
    m = re.search(r"^%s\b" % re.escape(START), doc, re.M)
    return doc[:m.start()].rstrip("\n") if m else doc.rstrip("\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="rewrite the OPTIONS block in every tool that needs one")
    ap.add_argument("--quiet", action="store_true", help="only print the summary line")
    a = ap.parse_args()

    bad = 0
    for d in sorted(os.listdir(ROOT)):
        sub = os.path.join(ROOT, d)
        if not os.path.isdir(sub) or d.startswith("."):
            continue
        for f in sorted(os.listdir(sub)):
            if not f.endswith(".py"):
                continue
            rel = "%s/%s" % (d, f)
            if rel in SKIP:
                continue
            path = os.path.join(sub, f)
            src = open(path).read()
            tree = ast.parse(src)
            doc = ast.get_docstring(tree)
            opts = options(path)
            if not opts or doc is None:
                continue
            want = block(opts)
            have = doc[doc.index(START):].rstrip() if START in doc else ""
            if have.strip() == want.strip():
                continue
            bad += 1
            if not a.write:
                missing = [n for n, _, _, _ in opts if n.split(",")[0] not in doc]
                if not a.quiet:
                    print("%-30s %d option%s undocumented%s"
                          % (rel, len(missing), "" if len(missing) == 1 else "s",
                             (": " + ", ".join(missing)) if missing else
                             " (block out of date)"))
                continue
            newdoc = current(doc) + "\n\n" + want + "\n"
            # Replace only the docstring node's own text.
            node = tree.body[0].value
            lines = src.split("\n")
            start = node.lineno - 1
            end = node.end_lineno - 1
            quote = '"""'
            src2 = "\n".join(lines[:start]) + ("\n" if start else "")
            src2 += quote + newdoc + quote + "\n"
            src2 += "\n".join(lines[end + 1:])
            ast.parse(src2)
            open(path, "w").write(src2)
            if not a.quiet:
                print("%-30s wrote %d options" % (rel, len(opts)))
    print("\n%d tool%s %s" % (bad, "" if bad == 1 else "s",
                              "updated" if a.write else "out of date"))
    return 1 if bad and not a.write else 0


if __name__ == "__main__":
    sys.exit(main())
