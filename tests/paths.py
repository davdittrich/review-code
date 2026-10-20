"""Importing this puts the scripts under test (and this directory) on sys.path."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(os.path.dirname(HERE), "skills", "review-code", "scripts"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
