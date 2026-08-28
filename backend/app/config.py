from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ROOT / ".env")
if os.getenv("SCL_SKIP_LOCAL_ENV", "false").lower() not in {"1", "true", "yes"}:
    load_dotenv(ROOT / ".env.local", override=True)
DEFAULT_DATABASE_URL = f"sqlite:///{(ROOT / 'data' / 'scl_catalog.db').as_posix()}"


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini" if os.getenv("GEMINI_API_KEY") else "openai")
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    gemini_embedding_model: str = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
    gemini_embedding_dimensions: int = int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "128"))
    gemini_daily_request_limit: int = int(os.getenv("GEMINI_DAILY_REQUEST_LIMIT", "20"))
    gemini_vector_index_path: str = os.getenv(
        "GEMINI_VECTOR_INDEX_PATH", str(ROOT / "data" / "gemini_vector_index.json")
    )
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_base_url: str | None = os.getenv("OPENAI_BASE_URL") or None
    openai_chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-5.6-luna")
    openai_reasoning_effort: str = os.getenv("OPENAI_REASONING_EFFORT", "low")
    openai_vector_store_id: str | None = os.getenv("OPENAI_VECTOR_STORE_ID")
    vector_search_enabled: bool = os.getenv("VECTOR_SEARCH_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    vector_search_shadow_mode: bool = os.getenv("VECTOR_SEARCH_SHADOW_MODE", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    vector_search_max_results: int = int(os.getenv("VECTOR_SEARCH_MAX_RESULTS", "12"))
    vector_search_min_score: float = float(os.getenv("VECTOR_SEARCH_MIN_SCORE", "0.25"))
    vector_search_timeout_seconds: float = float(os.getenv("VECTOR_SEARCH_TIMEOUT_SECONDS", "10"))
    allowed_origins: tuple[str, ...] = _csv(
        os.getenv(
            "ALLOWED_ORIGINS",
            (
                "http://localhost:5173,http://127.0.0.1:5173,"
                "http://localhost:4173,http://127.0.0.1:4173,"
                "http://localhost:8080,http://127.0.0.1:8080"
            ),
        )
    )
    max_input_chars: int = int(os.getenv("MAX_INPUT_CHARS", "500"))
    session_history_limit: int = int(os.getenv("SESSION_HISTORY_LIMIT", "8"))
    result_provider_mode: str = os.getenv("RESULT_PROVIDER_MODE", "unconfigured")
    result_api_base_url: str | None = os.getenv("RESULT_API_BASE_URL") or None
    result_api_auth_path: str = os.getenv("RESULT_API_AUTH_PATH", "/v1/auth/sessions")
    result_api_list_path: str = os.getenv("RESULT_API_LIST_PATH", "/v1/results")
    result_api_detail_path: str = os.getenv("RESULT_API_DETAIL_PATH", "/v1/results/{result_id}")
    result_api_logout_path: str = os.getenv("RESULT_API_LOGOUT_PATH", "/v1/auth/logout")
    result_api_client_id: str | None = os.getenv("RESULT_API_CLIENT_ID") or None
    result_api_client_secret: str | None = os.getenv("RESULT_API_CLIENT_SECRET") or None
    result_api_ca_bundle: str | None = os.getenv("RESULT_API_CA_BUNDLE") or None
    result_api_client_cert: str | None = os.getenv("RESULT_API_CLIENT_CERT") or None
    result_api_client_key: str | None = os.getenv("RESULT_API_CLIENT_KEY") or None
    result_api_timeout_seconds: float = float(os.getenv("RESULT_API_TIMEOUT_SECONDS", "15"))
    result_api_allow_http: bool = os.getenv("RESULT_API_ALLOW_HTTP", "false").lower() in {"1", "true", "yes"}
    result_session_ttl_minutes: int = int(os.getenv("RESULT_SESSION_TTL_MINUTES", "15"))
    field_encryption_key: str | None = os.getenv("FIELD_ENCRYPTION_KEY") or None
    attachment_max_bytes: int = int(os.getenv("ATTACHMENT_MAX_BYTES", str(100 * 1024 * 1024)))
    ocr_enabled: bool = os.getenv("OCR_ENABLED", "true").lower() in {"1", "true", "yes"}
    ocr_tesseract_cmd: str | None = os.getenv("OCR_TESSERACT_CMD") or None
    ocr_tessdata_dir: str | None = os.getenv("OCR_TESSDATA_DIR") or None
    ocr_languages: str = os.getenv("OCR_LANGUAGES", "kor+eng")
    ocr_dpi: int = int(os.getenv("OCR_DPI", "180"))
    ocr_timeout_seconds: int = int(os.getenv("OCR_TIMEOUT_SECONDS", "180"))
    ocr_max_pages: int = int(os.getenv("OCR_MAX_PAGES", "250"))
    ocr_max_image_pixels: int = int(os.getenv("OCR_MAX_IMAGE_PIXELS", "8000000"))
    ocr_page_segmentation_mode: int = int(os.getenv("OCR_PSM", "3"))
    office_converter_cmd: str | None = os.getenv("OFFICE_CONVERTER_CMD") or None
    office_converter_timeout_seconds: int = int(os.getenv("OFFICE_CONVERTER_TIMEOUT_SECONDS", "120"))
    request_timeout_seconds: float = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
    openai_max_retries: int = int(os.getenv("OPENAI_MAX_RETRIES", "0"))
    external_web_search_enabled: bool = os.getenv(
        "EXTERNAL_WEB_SEARCH_ENABLED", "false"
    ).lower() in {"1", "true", "yes"}
    external_web_search_model: str = os.getenv(
        "EXTERNAL_WEB_SEARCH_MODEL", os.getenv("OPENAI_CHAT_MODEL", "gpt-5.6-luna")
    )
    external_web_search_allowed_domains: tuple[str, ...] = _csv(
        os.getenv(
            "EXTERNAL_WEB_SEARCH_ALLOWED_DOMAINS",
            (
                "scllab.co.kr,kdca.go.kr,mfds.go.kr,hira.or.kr,"
                "pubmed.ncbi.nlm.nih.gov,clinicaltrials.gov,who.int,cdc.gov,fda.gov"
            ),
        )
    )
    external_web_search_timeout_seconds: float = float(
        os.getenv("EXTERNAL_WEB_SEARCH_TIMEOUT_SECONDS", "15")
    )
    database_url: str = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    seed_demo_on_empty: bool = os.getenv("SEED_DEMO_ON_EMPTY", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    scl_crawl_delay_seconds: float = float(os.getenv("SCL_CRAWL_DELAY_SECONDS", "0.25"))
    scl_crawl_page_size: int = int(os.getenv("SCL_CRAWL_PAGE_SIZE", "50"))
    scl_inactive_after_misses: int = int(os.getenv("SCL_INACTIVE_AFTER_MISSES", "3"))

    @property
    def mode(self) -> str:
        if self.llm_provider == "gemini" and self.gemini_api_key:
            return "gemini"
        return "openai" if self.openai_api_key else "demo_fallback"

    @property
    def chat_model(self) -> str:
        return self.gemini_model if self.mode == "gemini" else self.openai_chat_model

    @property
    def live_chat_available(self) -> bool:
        return self.mode in {"gemini", "openai"}

    @property
    def vector_search_configured(self) -> bool:
        if self.llm_provider == "gemini":
            return bool(
                self.vector_search_enabled
                and self.gemini_api_key
                and Path(self.gemini_vector_index_path).is_file()
            )
        return bool(
            self.vector_search_enabled
            and self.openai_api_key
            and self.openai_vector_store_id
        )

    @property
    def validated_reasoning_effort(self) -> str:
        allowed = {"none", "low", "medium", "high", "xhigh", "max"}
        return self.openai_reasoning_effort if self.openai_reasoning_effort in allowed else "low"

    @property
    def external_web_search_configured(self) -> bool:
        return bool(
            self.external_web_search_enabled
            and self.openai_api_key
            and self.external_web_search_allowed_domains
        )


settings = Settings()
