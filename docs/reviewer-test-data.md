# Reviewer test data

After cloning, run `git lfs pull` so the public SQLite snapshot and OCR language
models are materialized instead of remaining LFS pointers.

## Included

- `data/scl_catalog.snapshot.db.gz`: public catalog, locations, routes, documents,
  attachment metadata, and extracted attachment text. The backend creates an
  ignored writable `data/scl_catalog.db` from it on first startup.
- `data/evals/`: deterministic public-search, vector-search, and admin-chatbot cases.
- `data/fixtures/tests.json`: small offline catalog fixture.
- `data/scl-documents/public-faq.md`: public FAQ source used by the demo.
- `data/ocr/tessdata/`: English and Korean Tesseract language models.
- `chat_logs/`: sanitized project decision and implementation history. These logs
  are documentation and are not loaded by the application at runtime.

## Not included

- `.env`, encryption keys, credentials, or personal-result data.
- `data/attachments/`, a 2.57 GiB local download cache. Its searchable extracted
  text, metadata, and public source URLs are already present in the SQLite snapshot.
- generated caches, virtual environments, dependencies, and build output.

## Verification

Start the reviewer environment from the project root with the same `main` revision
and materialized Git LFS snapshot:

```powershell
git checkout main
git pull
git lfs pull
docker compose up --build
```

For the full local verification suite, run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests
python scripts/validate_public_data.py

cd frontend
npm ci
npm run lint
npm run test:unit
npm run test:sites
```

The OCR test runs when a Tesseract executable is available. The GitLab `ocr-test`
job installs the executable explicitly, so it must execute rather than skip.
