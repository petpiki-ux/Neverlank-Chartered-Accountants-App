"""Split one very large module into a package of smaller files that behave exactly like the original single file.

    python tools/split_module.py models.py --lines 1100
    python tools/split_module.py engagements.py --lines 1100

The original file is cut at the boundaries between top-level statements (a class, a function, a constant), never inside one; the text of
every statement and every comment is copied unchanged. The package's __init__.py loads the parts in their original order into ONE shared set
of names, which is what the single file was: a name defined in one part can be used in another, `from models import X` still works, and
`import engagements; engagements._helper` still finds everything, including names that start with an underscore.

It writes <name>/ next to the original and renames the original to <name>.py.single (kept, so the change can be undone by hand; delete the
folder and rename the file back).
"""
import argparse
import ast
import os
import re
import sys


def snake(name):
    return re.sub(r"[^a-z0-9]+", "_", re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()).strip("_")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module", help="path of the .py file to split")
    ap.add_argument("--lines", type=int, default=1100, help="aim for about this many lines per part")
    args = ap.parse_args(argv)

    path = os.path.abspath(args.module)
    base = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.join(os.path.dirname(path), base)
    if os.path.exists(folder):
        print("A folder named", folder, "already exists.")
        return 2
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")
    tree = ast.parse(text)

    # start line of every top-level statement (a decorated function starts at its first decorator)
    starts = []
    for node in tree.body:
        first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        starts.append((first, node))
    # a statement's text begins right after the previous statement ends, so the comments above it travel with it
    bounds = []
    prev_end = 0
    for first, node in starts:
        bounds.append((prev_end, node.end_lineno, node))
        prev_end = node.end_lineno
    tail_start = prev_end                      # trailing comments after the last statement

    parts, cur, cur_len = [], [], 0
    for lo, hi, node in bounds:
        n = hi - lo
        # the first part always holds the imports at the top of the file
        if cur and cur_len + n > args.lines and not (len(parts) == 0 and cur_len < 40):
            parts.append(cur)
            cur, cur_len = [], 0
        cur.append((lo, hi, node))
        cur_len += n
    if cur:
        parts.append(cur)

    os.makedirs(folder)
    names = []
    for i, part in enumerate(parts, 1):
        lo = part[0][0]
        hi = part[-1][1] + (len(lines) - part[-1][1] if i == len(parts) else 0)
        body = "\n".join(lines[lo:hi]).strip("\n")
        label = next((snake(n.name) for _l, _h, n in part if isinstance(n, (ast.ClassDef, ast.FunctionDef))), "setup")
        name = f"part_{i:02d}_{label}"[:60]
        names.append(name)
        classes = [n.name for _l, _h, n in part if isinstance(n, ast.ClassDef)]
        head = f'"""{base}, part {i} of {len(parts)}.  Holds: {", ".join(classes[:12]) + (" ..." if len(classes) > 12 else "") if classes else "functions and constants"}.\n\nSee __init__.py: all parts share one set of names, exactly as the single file {base}.py did."""\n'
        if i == 1:
            head = ""                          # the module's own docstring/imports stay first
        with open(os.path.join(folder, name + ".py"), "w", encoding="utf-8") as fh:
            fh.write(head + ("\n" if head else "") + body + "\n")

    init = f'''"""{base}: loaded from {len(parts)} part files that share one set of names (see tools/split_module.py).

Part 1 holds the imports. Each later part is run with every name the earlier parts defined already in place, then, once all parts have run,
every part is given the names of all the others (so a function in one part can call one defined in a later part, as in a single file).
`import {base}` and `from {base} import anything` behave as before.
"""
import importlib.util
import os
import sys

_PARTS = {names!r}
_here = os.path.dirname(os.path.abspath(__file__))
_modules = []
_shared = {{}}

for _name in _PARTS:
    _full = f"{{__name__}}.{{_name}}"
    _spec = importlib.util.find_spec(_full)
    _mod = importlib.util.module_from_spec(_spec)
    _mod.__dict__.update(_shared)                 # names from the earlier parts, as in one file
    sys.modules[_full] = _mod
    _spec.loader.exec_module(_mod)
    _shared.update({{k: v for k, v in vars(_mod).items() if not (k.startswith("__") and k.endswith("__"))}})
    _modules.append(_mod)

for _mod in _modules:                             # call-time lookups: every part sees every name
    for _k, _v in _shared.items():
        _mod.__dict__[_k] = _v
globals().update(_shared)
del _name, _full, _spec, _mod, _k, _v
'''
    with open(os.path.join(folder, "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write(init)
    os.rename(path, path + ".single")
    print(f"{base}.py -> {base}/ with {len(parts)} parts:")
    for n, p in zip(names, parts):
        print(f"  {n}.py  ({p[-1][1] - p[0][0]} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
