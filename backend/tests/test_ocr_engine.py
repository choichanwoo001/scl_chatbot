from __future__ import annotations

import io

import pytest
from app.config import Settings
from app.ocr_engine import TesseractOCREngine
from PIL import Image, ImageDraw, ImageFont


def test_local_tesseract_recognizes_generated_image_when_available() -> None:
    engine = TesseractOCREngine(Settings(ocr_languages="kor+eng"))
    status = engine.status()
    if not status["available"]:
        pytest.skip(str(status))
    image = Image.new("RGB", (1400, 300), "white")
    ImageDraw.Draw(image).text(
        (50, 80), "SCL OCR TEST 12345", fill="black", font=ImageFont.load_default(size=72)
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG")

    text = engine.ocr_image(buffer.getvalue())

    assert "SCL OCR TEST" in text
    assert "12345" in text


def test_ocr_status_reports_missing_explicit_executable() -> None:
    engine = TesseractOCREngine(Settings(ocr_tesseract_cmd="Z:/missing/tesseract.exe"))

    status = engine.status()

    assert status["available"] is False
    assert status["executable"] is None
