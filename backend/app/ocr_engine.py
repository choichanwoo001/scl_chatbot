from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from .config import ROOT, Settings


class OCRUnavailable(RuntimeError):
    pass


class OCRProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRPage:
    text: str
    page_number: int | None = None


class TesseractOCREngine:
    """Local-only OCR. Document bytes never leave the backend host."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.executable = self._resolve_executable(settings.ocr_tesseract_cmd)
        self.tessdata_dir = self._resolve_tessdata(settings.ocr_tessdata_dir, self.executable)
        self._validated = False

    @property
    def name(self) -> str:
        return "tesseract"

    def status(self) -> dict[str, object]:
        try:
            languages = self._available_languages()
            requested = self._requested_languages()
            missing = sorted(requested - languages)
            return {
                "available": not missing,
                "executable": str(self.executable) if self.executable else None,
                "tessdata_dir": str(self.tessdata_dir) if self.tessdata_dir else None,
                "requested_languages": sorted(requested),
                "available_languages": sorted(languages),
                "missing_languages": missing,
            }
        except OCRUnavailable as error:
            return {
                "available": False,
                "executable": str(self.executable) if self.executable else None,
                "tessdata_dir": str(self.tessdata_dir) if self.tessdata_dir else None,
                "requested_languages": sorted(self._requested_languages()),
                "available_languages": [],
                "missing_languages": sorted(self._requested_languages()),
                "error": str(error),
            }

    def ocr_image(self, content: bytes) -> str:
        self._validate()
        try:
            with Image.open(io.BytesIO(content)) as source:
                image = ImageOps.exif_transpose(source).convert("L")
                if image.width * image.height > self.settings.ocr_max_image_pixels:
                    scale = math.sqrt(self.settings.ocr_max_image_pixels / (image.width * image.height))
                    image = image.resize(
                        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                image = ImageOps.autocontrast(image)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=True)
        except Exception as error:
            raise OCRProcessingError(f"이미지 디코딩 실패: {type(error).__name__}") from error
        return self._run_tesseract(buffer.getvalue())

    def ocr_pdf(self, content: bytes) -> list[OCRPage]:
        self._validate()
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
        except Exception as error:
            raise OCRProcessingError(f"PDF 렌더링 준비 실패: {type(error).__name__}") from error
        try:
            if document.page_count > self.settings.ocr_max_pages:
                raise OCRProcessingError(
                    f"OCR 페이지 제한 초과: {document.page_count}>{self.settings.ocr_max_pages}"
                )
            pages: list[OCRPage] = []
            for index, page in enumerate(document, 1):
                pixmap = page.get_pixmap(
                    dpi=self.settings.ocr_dpi,
                    colorspace=pymupdf.csGRAY,
                    alpha=False,
                )
                text = self.ocr_image(pixmap.tobytes("png"))
                if text.strip():
                    pages.append(OCRPage(text=text, page_number=index))
            return pages
        finally:
            document.close()

    def _run_tesseract(self, image: bytes) -> str:
        if not self.executable:
            raise OCRUnavailable("Tesseract 실행 파일을 찾을 수 없습니다.")
        command = [
            str(self.executable),
            "stdin",
            "stdout",
            "-l",
            self.settings.ocr_languages,
            "--psm",
            str(self.settings.ocr_page_segmentation_mode),
            "--dpi",
            str(self.settings.ocr_dpi),
            "quiet",
        ]
        environment = os.environ.copy()
        environment["OMP_THREAD_LIMIT"] = "1"
        if self.tessdata_dir:
            environment["TESSDATA_PREFIX"] = str(self.tessdata_dir)
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(
                command,
                input=image,
                capture_output=True,
                timeout=self.settings.ocr_timeout_seconds,
                check=False,
                env=environment,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as error:
            raise OCRProcessingError("OCR 처리 시간이 제한을 초과했습니다.") from error
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise OCRProcessingError(f"Tesseract 오류({result.returncode}): {detail[:300]}")
        return result.stdout.decode("utf-8", errors="replace").strip()

    def _validate(self) -> None:
        if self._validated:
            return
        languages = self._available_languages()
        missing = self._requested_languages() - languages
        if missing:
            raise OCRUnavailable(f"OCR 언어 모델이 없습니다: {', '.join(sorted(missing))}")
        self._validated = True

    def _available_languages(self) -> set[str]:
        if not self.executable:
            raise OCRUnavailable("Tesseract 실행 파일을 찾을 수 없습니다.")
        environment = os.environ.copy()
        if self.tessdata_dir:
            environment["TESSDATA_PREFIX"] = str(self.tessdata_dir)
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(
                [str(self.executable), "--list-langs"],
                capture_output=True,
                timeout=15,
                check=False,
                env=environment,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OCRUnavailable("Tesseract 상태를 확인할 수 없습니다.") from error
        if result.returncode:
            raise OCRUnavailable(result.stderr.decode("utf-8", errors="replace")[:300])
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        return {line.strip() for line in lines if line.strip() and not line.startswith("List of")}

    def _requested_languages(self) -> set[str]:
        return {item.strip() for item in self.settings.ocr_languages.split("+") if item.strip()}

    @staticmethod
    def _resolve_executable(configured: str | None) -> Path | None:
        if configured:
            path = Path(configured).expanduser()
            return path if path.is_file() else None
        located = shutil.which("tesseract")
        if located:
            return Path(located)
        candidates = []
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            root = os.getenv(key)
            if root:
                candidates.extend(
                    [
                        Path(root) / "Tesseract-OCR" / "tesseract.exe",
                        Path(root) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
                    ]
                )
        return next((path for path in candidates if path.is_file()), None)

    @staticmethod
    def _resolve_tessdata(configured: str | None, executable: Path | None) -> Path | None:
        candidates = []
        if configured:
            candidates.append(Path(configured).expanduser())
        candidates.append(ROOT / "data" / "ocr" / "tessdata")
        if executable:
            candidates.append(executable.parent / "tessdata")
        return next((path for path in candidates if path.is_dir()), None)
