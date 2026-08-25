# Public data snapshot

`data/scl_catalog.snapshot.db.gz` is a compressed, versioned SQLite snapshot of data
collected from the public SCL website. Git LFS stores the binary while Git tracks a
small pointer.

On first startup, when `DATABASE_URL` is not set and `data/scl_catalog.db` does not
exist, the backend decompresses the snapshot to that ignored runtime path. The
application can then use WAL mode without modifying the versioned snapshot.

The snapshot includes the public test catalog and detail-page guidance, locations, routes,
documents, attachment metadata, and extracted attachment text. It does not contain handoff
requests or chat feedback. The original downloaded attachments under
`data/attachments/`, OCR runtime files under `data/ocr/`, local encryption keys,
and WAL/SHM files are not versioned.

Run the same integrity check used by CI after starting from a clone without a
runtime DB. The backend will first create the ignored writable copy:

```powershell
$env:PYTHONPATH = "backend"
python scripts/validate_public_data.py
```

When refreshing the snapshot, use SQLite's backup API (or stop the backend and
checkpoint WAL first), verify the private-workflow tables are empty, run the
integrity check, and then commit the new LFS object.

```powershell
.venv\Scripts\python scripts\build_public_snapshot.py
```

The snapshot builder uses SQLite's online backup API, removes handoff requests,
chat feedback, and learned FAQ candidates from the copy, validates integrity,
and only then replaces the compressed public snapshot.
