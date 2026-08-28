from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "scl_catalog.db"
OUTPUT = ROOT / "data" / "gemini_vector_index.json"


def load_records() -> list[dict[str, str]]:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT 'document' AS entity_type, id, title,
               COALESCE(summary, substr(body_text, 1, 2400), '') AS snippet,
               content_hash
        FROM public_documents
        WHERE status = 'active'
        UNION ALL
        SELECT 'attachment', da.id, da.file_name, substr(ac.extracted_text, 1, 2400),
               COALESCE(ac.content_hash, da.content_hash, '')
        FROM document_attachments da
        JOIN attachment_contents ac ON ac.attachment_id = da.id
        JOIN public_documents pd ON pd.id = da.document_id
        WHERE da.status = 'active' AND pd.status = 'active'
          AND ac.extraction_status = 'extracted' AND length(trim(ac.extracted_text)) > 0
        ORDER BY entity_type, id DESC
        """
    ).fetchall()
    connection.close()
    records: list[dict[str, str]] = []
    for row in rows:
        entity_type = str(row["entity_type"])
        entity_id = str(row["id"])
        title = str(row["title"] or "").strip()
        snippet = " ".join(str(row["snippet"] or "").split())
        content_hash = str(row["content_hash"] or "") or hashlib.sha256(
            f"{title}\n{snippet}".encode("utf-8")
        ).hexdigest()
        records.append(
            {
                "ref": f"{entity_type}:{entity_id}",
                "entity_type": entity_type,
                "entity_id": entity_id,
                "title": title,
                "snippet": snippet[:900],
                "embedding_text": f"title: {title} | text: {snippet[:2400]}",
                "content_hash": content_hash,
            }
        )
    return records


def quantize(values: list[float]) -> str:
    norm = math.sqrt(sum(float(value) ** 2 for value in values)) or 1.0
    encoded = bytes((max(-127, min(127, round(float(value) / norm * 127))) % 256) for value in values)
    return base64.b64encode(encoded).decode("ascii")


def embed_batch(
    client: httpx.Client,
    key: str,
    model: str,
    dimensions: int,
    records: list[dict[str, str]],
) -> list[list[float]]:
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
    body = {
        "requests": [
            {
                "model": f"models/{model}",
                "content": {"parts": [{"text": item["embedding_text"]}]},
                "outputDimensionality": dimensions,
            }
            for item in records
        ]
    }
    for attempt in range(7):
        try:
            response = client.post(endpoint, headers={"x-goog-api-key": key}, json=body)
        except httpx.RequestError:
            if attempt == 6:
                raise
            time.sleep(min(30, 2 ** (attempt + 1)))
            continue
        if response.status_code != 429:
            response.raise_for_status()
            embeddings = response.json().get("embeddings") or []
            values = [list(item.get("values") or []) for item in embeddings]
            if len(values) != len(records) or any(len(item) != dimensions for item in values):
                raise RuntimeError("Gemini batch embedding response shape did not match the request")
            return values
        wait_seconds = min(60, max(2, int(response.headers.get("retry-after") or 2 ** (attempt + 1))))
        time.sleep(wait_seconds)
    raise RuntimeError("Gemini embedding quota remained unavailable after retries")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--max-per-type",
        type=int,
        default=300,
        help="Newest records to embed per entity type; 0 indexes the full corpus.",
    )
    args = parser.parse_args()
    config = {**dotenv_values(ROOT / ".env"), **dotenv_values(ROOT / ".env.local")}
    key = config.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is required in .env.local")
    model = str(config.get("GEMINI_EMBEDDING_MODEL") or "gemini-embedding-2")
    dimensions = int(config.get("GEMINI_EMBEDDING_DIMENSIONS") or 128)
    records = load_records()
    if args.max_per_type > 0:
        selected: list[dict[str, str]] = []
        counts: dict[str, int] = {}
        for record in records:
            entity_type = record["entity_type"]
            if counts.get(entity_type, 0) >= args.max_per_type:
                continue
            counts[entity_type] = counts.get(entity_type, 0) + 1
            selected.append(record)
        records = selected
    existing: dict[str, dict[str, str]] = {}
    if OUTPUT.is_file():
        prior = json.loads(OUTPUT.read_text(encoding="utf-8"))
        if prior.get("model") == model and int(prior.get("dimensions") or 0) == dimensions:
            existing = {str(item["ref"]): item for item in prior.get("items", [])}

    completed: dict[str, dict[str, str]] = {}
    pending: list[dict[str, str]] = []
    for record in records:
        prior = existing.get(record["ref"])
        if prior and prior.get("content_hash") == record["content_hash"] and prior.get("vector"):
            completed[record["ref"]] = prior
        else:
            pending.append(record)

    with httpx.Client(timeout=90) as client:
        for start in range(0, len(pending), max(1, args.batch_size)):
            batch = pending[start : start + max(1, args.batch_size)]
            vectors = embed_batch(client, key, model, dimensions, batch)
            for record, vector in zip(batch, vectors, strict=True):
                item = {key: value for key, value in record.items() if key != "embedding_text"}
                item["vector"] = quantize(vector)
                completed[record["ref"]] = item
            progress = {
                "model": model,
                "dimensions": dimensions,
                "created_at": datetime.now(UTC).isoformat(),
                "items": list(completed.values()),
            }
            OUTPUT.write_text(
                json.dumps(progress, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            done = min(start + len(batch), len(pending))
            print(f"Embedded {done}/{len(pending)} changed records")

    ordered = [completed[record["ref"]] for record in records]
    payload = {
        "model": model,
        "dimensions": dimensions,
        "created_at": datetime.now(UTC).isoformat(),
        "items": ordered,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(ordered)} vectors to {OUTPUT}")


if __name__ == "__main__":
    main()
