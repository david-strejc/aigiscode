from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Ensure the current Python interpreter's bin directory is on PATH so that
# tools installed alongside the interpreter (e.g. ruff) can be discovered
# by shutil.which() at runtime.  Use the non-resolved path to preserve
# the venv's bin directory (resolved paths follow symlinks to /usr/bin).
_interpreter_bin = str(Path(sys.executable).parent)
if _interpreter_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _interpreter_bin + os.pathsep + os.environ.get("PATH", "")
