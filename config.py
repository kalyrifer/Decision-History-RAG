import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
LLM_FREE_SUFFIX = ":free"

PG_DSN = os.getenv("PG_DSN", "postgresql://rag:rag@localhost:5433/decision_rag")

TARGET_REPO = "pydantic/pydantic"
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
REPO_GIT_DIR = DATA_DIR / "repo.git"

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

FTS_WEIGHT = 0.1  # вес FTS-канала в RRF fusion (понижен: FTS на этом корпусе шумит)

RERANK_MODEL = "BAAI/bge-reranker-base"
RERANK_DEVICE = "cpu"  # "cuda" при наличии GPU-версии torch
