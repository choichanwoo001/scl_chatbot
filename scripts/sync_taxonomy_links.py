from __future__ import annotations

import json

from app.taxonomy_curator import sync_curated_taxonomy

if __name__ == "__main__":
    print(json.dumps(sync_curated_taxonomy(), ensure_ascii=False, indent=2))
