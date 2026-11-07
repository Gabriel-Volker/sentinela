"""tests/conftest.py — Fixtures compartilhadas."""

import sys
from pathlib import Path

# Garante que o projeto está no path
sys.path.insert(0, str(Path(__file__).parent.parent))
