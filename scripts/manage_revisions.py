"""Inspect ingestion revisions and optionally archive old history before pruning."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.repositories.revisions import RevisionRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--entity-type", help="test, container, public_document, or another stored entity type"
    )
    parser.add_argument("--entity-id", type=int)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument(
        "--archive", type=Path, help="New JSONL archive file; existing files are never overwritten"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Prune archived old revisions; latest revision is retained"
    )
    args = parser.parse_args()
    if bool(args.entity_type) != (args.entity_id is not None):
        parser.error("--entity-type and --entity-id must be provided together")
    repository = RevisionRepository()
    if args.entity_type:
        if args.apply or args.archive:
            parser.error("Entity inspection cannot be combined with retention actions")
        result = repository.list_revisions(args.entity_type, args.entity_id)
    else:
        result = repository.retention(args.days, archive=args.archive, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
