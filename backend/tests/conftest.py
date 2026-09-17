import os

# Keep unit tests isolated from the locally crawled public catalog.
os.environ["SCL_SKIP_LOCAL_ENV"] = "true"
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_DEMO_ON_EMPTY"] = "true"
# Unit tests must stay deterministic and must never spend API credits.
os.environ["GEMINI_API_KEY"] = ""
os.environ["LLM_PROVIDER"] = "gemini"
