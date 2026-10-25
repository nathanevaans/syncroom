#!/usr/bin/env python3
"""Launcher so `python3 syncroom.py run ...` keeps working. The code lives in the `syncroom/`
package; `python3 -m syncroom run ...` (or `syncroom run ...` after `pip install -e .`) is the
same."""

from syncroom.cli import main

if __name__ == "__main__":
    main()
