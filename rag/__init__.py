__version__ = "0.1.0"
PARSER_VERSION = "pymupdf-extract-1"

import os as _os
from pathlib import Path as _Path

# Folder model lokal (lihat scripts/download_models.py). Bisa diganti lewat env RAG_MODELS.
MODELS_DIR = _Path(_os.environ.get("RAG_MODELS", _Path(__file__).resolve().parents[1] / "models"))

