import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
paths = [
    ROOT / "apps" / "api",
    ROOT / "packages" / "shared" / "src",
    ROOT / "packages" / "security" / "src",
    ROOT / "packages" / "ssg" / "src",
]
for p in paths:
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
