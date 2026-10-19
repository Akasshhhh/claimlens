from __future__ import annotations

import json
import os

from claimlens.loaders import csv_hashes


if __name__ == "__main__":
    print(json.dumps(csv_hashes(os.getenv("CLAIMLENS_DATA_DIR", ".")), indent=2, sort_keys=True))
