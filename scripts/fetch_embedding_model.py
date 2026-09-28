"""Download the pinned local embedding model once (explicit operator step).

The runtime never downloads anything: ``LocalEmbeddingProvider`` fails closed
when these files are missing or their SHA-256 does not match. Only two files
are fetched — the int8-quantized ONNX encoder and its tokenizer vocabulary —
from one pinned Hugging Face revision.

    uv run python scripts/fetch_embedding_model.py [--target data/models/all-MiniLM-L6-v2]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_bot.memory.embeddings import (
    MODEL_FILES,
    MODEL_REPOSITORY,
    MODEL_REVISION,
)

BASE_URL = "https://huggingface.co/{repo}/resolve/{revision}/{path}"


def fetch(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for local_name, (remote_path, expected_sha256) in MODEL_FILES.items():
            destination = target / local_name
            if destination.exists() and _sha256(destination) == expected_sha256:
                print(f"ok (cached) {local_name}")
                continue
            url = BASE_URL.format(repo=MODEL_REPOSITORY, revision=MODEL_REVISION, path=remote_path)
            partial = destination.with_suffix(destination.suffix + ".partial")
            digest = hashlib.sha256()
            with client.stream("GET", url) as response, partial.open("wb") as file:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    digest.update(chunk)
                    file.write(chunk)
            if digest.hexdigest() != expected_sha256:
                partial.unlink(missing_ok=True)
                raise SystemExit(f"checksum mismatch for {local_name}; nothing installed")
            partial.replace(destination)
            print(f"ok {local_name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("data/models/all-MiniLM-L6-v2"))
    fetch(parser.parse_args().target)
