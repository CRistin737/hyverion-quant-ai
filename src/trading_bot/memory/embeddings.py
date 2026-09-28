"""Minimal local embedding provider (embedding-only, in-process, offline).

Deliberately stripped down: one int8-quantized sentence encoder
(all-MiniLM-L6-v2, 384 dims) run by ``onnxruntime``, and a self-contained BERT
WordPiece tokenizer instead of a tokenizer library, so no model hub client,
no network access and no text generation exist in this path. Model files are
fetched once by ``scripts/fetch_embedding_model.py`` and verified by SHA-256
every time they are loaded; missing or altered files fail closed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

MODEL_REPOSITORY = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_NAME = "all-MiniLM-L6-v2-qint8"
EMBEDDING_DIMENSIONS = 384
MAX_TOKENS = 128
MAX_BATCH = 32
MAX_TEXT_CHARS = 4000

# local file name -> (path in the pinned revision, SHA-256)
MODEL_FILES: dict[str, tuple[str, str]] = {
    "model.onnx": (
        "onnx/model_qint8_arm64.onnx",
        "4278337fd0ff3c68bfb6291042cad8ab363e1d9fbc43dcb499fe91c871902474",
    ),
    "tokenizer.json": (
        "tokenizer.json",
        "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    ),
}


class EmbeddingUnavailable(RuntimeError):
    """The local model is missing, altered or its runtime is not installed."""


class WordPieceTokenizer:
    """BERT uncased tokenization matching the model's ``tokenizer.json``."""

    def __init__(self, vocab: dict[str, int], *, max_tokens: int = MAX_TOKENS) -> None:
        for special in ("[CLS]", "[SEP]", "[UNK]", "[PAD]"):
            if special not in vocab:
                raise EmbeddingUnavailable(f"vocabulary is missing {special}")
        self._vocab = vocab
        self._max_tokens = max_tokens

    @classmethod
    def from_file(cls, path: Path) -> WordPieceTokenizer:
        data = json.loads(path.read_text(encoding="utf-8"))
        model = data.get("model") or {}
        vocab = model.get("vocab")
        if model.get("type") != "WordPiece" or not isinstance(vocab, dict):
            raise EmbeddingUnavailable("tokenizer.json is not a WordPiece vocabulary")
        return cls({str(token): int(index) for token, index in vocab.items()})

    def encode(self, text: str) -> list[int]:
        pieces: list[int] = []
        for word in _basic_tokens(text):
            pieces.extend(self._wordpiece(word))
        pieces = pieces[: self._max_tokens - 2]
        return [self._vocab["[CLS]"], *pieces, self._vocab["[SEP]"]]

    def _wordpiece(self, word: str) -> list[int]:
        if len(word) > 100:
            return [self._vocab["[UNK]"]]
        ids: list[int] = []
        start = 0
        while start < len(word):
            end = len(word)
            match: int | None = None
            while start < end:
                candidate = word[start:end] if start == 0 else f"##{word[start:end]}"
                match = self._vocab.get(candidate)
                if match is not None:
                    break
                end -= 1
            if match is None:
                return [self._vocab["[UNK]"]]
            ids.append(match)
            start = end
        return ids


def _basic_tokens(text: str) -> list[str]:
    cleaned: list[str] = []
    for char in text:
        code = ord(char)
        if code in (0, 0xFFFD) or _is_control(char):
            continue
        if _is_chinese(code):
            cleaned.extend((" ", char, " "))
        elif _is_whitespace(char):
            cleaned.append(" ")
        else:
            cleaned.append(char)
    normalized = unicodedata.normalize("NFD", "".join(cleaned).lower())
    stripped = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    tokens: list[str] = []
    for word in stripped.split():
        current = ""
        for char in word:
            if _is_punctuation(char):
                if current:
                    tokens.append(current)
                    current = ""
                tokens.append(char)
            else:
                current += char
        if current:
            tokens.append(current)
    return tokens


def _is_whitespace(char: str) -> bool:
    return char in " \t\n\r" or unicodedata.category(char) == "Zs"


def _is_control(char: str) -> bool:
    if char in "\t\n\r":
        return False
    return unicodedata.category(char) in {"Cc", "Cf"}


def _is_punctuation(char: str) -> bool:
    code = ord(char)
    if 33 <= code <= 47 or 58 <= code <= 64 or 91 <= code <= 96 or 123 <= code <= 126:
        return True
    return unicodedata.category(char).startswith("P")


def _is_chinese(code: int) -> bool:
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0xF900 <= code <= 0xFAFF
        or 0x2F800 <= code <= 0x2FA1F
    )


class LocalEmbeddingProvider:
    """Implements ``EmbeddingProvider`` with the pinned local model."""

    model = MODEL_NAME

    def __init__(self, model_dir: Path) -> None:
        for name, (_, expected) in MODEL_FILES.items():
            path = model_dir / name
            if not path.is_file():
                raise EmbeddingUnavailable(
                    f"{name} missing; run scripts/fetch_embedding_model.py"
                )
            if _sha256(path) != expected:
                raise EmbeddingUnavailable(f"{name} checksum mismatch; refusing to load")
        try:
            import numpy
            import onnxruntime
        except ImportError as exc:
            raise EmbeddingUnavailable("install the 'embeddings' extra") from exc
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._numpy: Any = numpy
        self._session: Any = onnxruntime.InferenceSession(
            str(model_dir / "model.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._inputs = {item.name for item in self._session.get_inputs()}
        self._tokenizer = WordPieceTokenizer.from_file(model_dir / "tokenizer.json")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > MAX_BATCH:
            raise ValueError(f"at most {MAX_BATCH} texts per call")
        if any(not text.strip() or len(text) > MAX_TEXT_CHARS for text in texts):
            raise ValueError(f"texts must be non-empty and at most {MAX_TEXT_CHARS} characters")
        return await asyncio.to_thread(self._embed_sync, list(texts))

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        np = self._numpy
        encoded = [self._tokenizer.encode(text) for text in texts]
        width = max(len(ids) for ids in encoded)
        input_ids = np.zeros((len(encoded), width), dtype=np.int64)
        attention = np.zeros((len(encoded), width), dtype=np.int64)
        for row, ids in enumerate(encoded):
            input_ids[row, : len(ids)] = ids
            attention[row, : len(ids)] = 1
        feeds = {"input_ids": input_ids, "attention_mask": attention}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        hidden = self._session.run(None, feeds)[0]
        mask = attention[:, :, None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
        vectors = pooled / norms
        if vectors.shape[1] != EMBEDDING_DIMENSIONS:
            raise EmbeddingUnavailable("unexpected embedding dimensions")
        return [[float(value) for value in vector] for vector in vectors]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
