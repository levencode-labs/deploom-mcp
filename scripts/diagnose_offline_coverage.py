"""Checkout entry point for the installed DepLoom saved-run diagnostic."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from deploom_mcp.coverage_diagnosis import diagnose, main


if __name__ == "__main__":
    main()
