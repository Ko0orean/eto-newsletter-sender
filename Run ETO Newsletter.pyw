#!/usr/bin/env pythonw
"""Double-click launcher for the ETO Newsletter Sender.

On Windows, double-clicking this file runs the app without a console window
(it is opened by pythonw.exe). If double-clicking does not work, use
``Run ETO Newsletter.bat`` instead, which shows any error messages.
"""
import os
import sys

# Make sure the project folder is importable when launched by double-click,
# regardless of the current working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eto_newsletter.app import main

if __name__ == "__main__":
    raise SystemExit(main())
