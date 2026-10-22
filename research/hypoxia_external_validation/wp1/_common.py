"""Path bootstrap for WP1 scripts (run with python -I, so sys.path is set explicitly)."""
import sys
from pathlib import Path

WP1 = Path(__file__).resolve().parent
BACKEND = Path(r"C:\Users\AgenticDev\OutdoorKoiFYP_research\Backend")
for p in (str(WP1), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)
SEED = 20261022
