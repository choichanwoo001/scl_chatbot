from __future__ import annotations

import argparse
import json

from app.config import settings
from app.scl_public_data import SCLPublicDataSync


def main() -> None:
    parser = argparse.ArgumentParser(description="SCL 공개 구조화 데이터를 RDB에 동기화합니다.")
    parser.add_argument(
        "dataset",
        choices=[
            "containers",
            "notices",
            "preservatives",
            "resources",
            "taxonomy",
            "locations",
            "routes",
            "content",
            "faqs",
            "all",
        ],
    )
    args = parser.parse_args()
    sync = SCLPublicDataSync(settings)
    actions = {
        "containers": sync.sync_containers,
        "notices": sync.sync_notices,
        "preservatives": sync.sync_preservatives,
        "resources": sync.sync_resources,
        "taxonomy": sync.sync_taxonomy,
        "locations": sync.sync_locations,
        "routes": sync.sync_routes,
        "content": sync.sync_content,
        "faqs": sync.sync_faqs,
    }
    selected = list(actions) if args.dataset == "all" else [args.dataset]
    results = [actions[name]() for name in selected]
    output = [result.__dict__ for result in results]
    print(json.dumps(output[0] if len(output) == 1 else output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
