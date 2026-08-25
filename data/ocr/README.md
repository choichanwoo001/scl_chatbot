# OCR runtime data

This directory contains the English and Korean `tessdata_fast` language models used
by the local Tesseract integration. The backend discovers
`data/ocr/tessdata` automatically and passes it through `TESSDATA_PREFIX`.

The language models do not include the Tesseract executable. Install Tesseract 5
locally, set `OCR_TESSERACT_CMD` when it is not on `PATH`, or use the backend Docker
image. The Docker image installs Tesseract itself; the repository provides the
matching testable language data for local and CI use.

Source: <https://github.com/tesseract-ocr/tessdata_fast>

License: Apache License 2.0; see `../LICENSE.tessdata_fast`.
