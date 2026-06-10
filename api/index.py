"""Vercel serverless entry point — exposes the Flask app as a handler."""

import sys
from pathlib import Path

# Ensure the project src directory is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

