"""Build a standalone Windows executable with PyInstaller.

Usage:
    pip install pyinstaller
    python build_exe.py

Output:
    dist/ETO Newsletter Sender/ETO Newsletter Sender.exe
"""
from __future__ import annotations

import PyInstaller.__main__

PyInstaller.__main__.run(
    [
        "eto_newsletter/__main__.py",
        "--name=ETO Newsletter Sender",
        "--windowed",          # no console window
        "--noconfirm",
        "--clean",
        # keyring's Windows backend is imported dynamically; include it.
        "--hidden-import=keyring.backends.Windows",
        "--hidden-import=win32ctypes.pywin32",
        "--collect-submodules=keyring",
    ]
)
