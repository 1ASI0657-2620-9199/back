"""Always exercise the checked-out common library, without a global install."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libraries/finanzen_common/src"))
