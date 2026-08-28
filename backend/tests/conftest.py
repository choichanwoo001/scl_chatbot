import os

# Keep unit tests isolated from the locally crawled public catalog.
os.environ["SCL_SKIP_LOCAL_ENV"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_DEMO_ON_EMPTY"] = "true"
# Unit tests must stay deterministic and must never spend API credits.
# Live OpenAI coverage is isolated in scripts/evaluate_openai_live.py.
os.environ["OPENAI_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["LLM_PROVIDER"] = "openai"
