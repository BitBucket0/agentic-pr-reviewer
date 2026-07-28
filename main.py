#!/usr/bin/env python3
"""Thin shim so `python main.py` still works; real logic lives in reviewer.cli."""

from reviewer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
