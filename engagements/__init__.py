"""engagements: loaded from 7 part files that share one set of names (see tools/split_module.py).

Part 1 holds the imports. Each later part is run with every name the earlier parts defined already in place, then, once all parts have run,
every part is given the names of all the others (so a function in one part can call one defined in a later part, as in a single file).
`import engagements` and `from engagements import anything` behave as before.
"""
import importlib.util
import os
import sys

_PARTS = ['part_01_access_dashboard_list', 'part_02_view_and_checklists', 'part_03_entity_checklists', 'part_04_tasks_and_signoffs', 'part_05_trial_balance_and_mapping', 'part_06_statements_and_ppe', 'part_07_report_generators']
_here = os.path.dirname(os.path.abspath(__file__))
_modules = []
_shared = {}

for _name in _PARTS:
    _full = f"{__name__}.{_name}"
    _spec = importlib.util.find_spec(_full)
    _mod = importlib.util.module_from_spec(_spec)
    _mod.__dict__.update(_shared)                 # names from the earlier parts, as in one file
    sys.modules[_full] = _mod
    _spec.loader.exec_module(_mod)
    _shared.update({k: v for k, v in vars(_mod).items() if not (k.startswith("__") and k.endswith("__"))})
    _modules.append(_mod)

for _mod in _modules:                             # call-time lookups: every part sees every name
    for _k, _v in _shared.items():
        _mod.__dict__[_k] = _v
globals().update(_shared)
del _name, _full, _spec, _mod, _k, _v
