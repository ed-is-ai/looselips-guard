#!/usr/bin/env python3
"""Run every tests/test_*.py. `python3 tests/run.py`, or run one file directly."""
import glob
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for path in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "test_*.py"))):
    name = os.path.splitext(os.path.basename(path))[0]
    importlib.import_module(name).run_tests()
    print(f"  {name} ok")

print("ok")
