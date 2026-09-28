from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from trading_bot.memory.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingUnavailable,
    LocalEmbeddingProvider,
    WordPieceTokenizer,
)
from trading_bot.memory.vector import NullVectorBackend, _vector_literal

MODEL_DIR = Path("data/models/all-MiniLM-L6-v2")
HAS_MODEL = (MODEL_DIR / "model.onnx").is_file() and (MODEL_DIR / "tokenizer.json").is_file()
HAS_RUNTIME = importlib.util.find_spec("onnxruntime") is not None
needs_runtime = pytest.mark.skipif(not HAS_RUNTIME, reason="install the 'embeddings' extra")
needs_model = pytest.mark.skipif(
    not HAS_MODEL, reason="run scripts/fetch_embedding_model.py to enable"
)

# Reference ids produced by the official tokenizer for the pinned vocabulary.
GOLDEN = {
    "La ruptura sin volumen se revirtió; stop @ 99.5 (\u22121.2%)": [
        101, 2474, 21766, 13876, 4648, 8254, 3872, 2078, 7367, 7065, 4313, 3775, 2080,
        1025, 2644, 1030, 5585, 1012, 1019, 1006, 1597, 2487, 1012, 1016, 1003, 1007, 102,
    ],
    "ÀÉÎÕÜ ñandú Straße ﬁnance 🚀": [
        101, 29347, 3695, 2226, 16660, 8566, 2358, 27807, 1984, 7229, 3401, 100, 102,
    ],
    "中文 mixed": [101, 1746, 1861, 3816, 102],
}


def test_provider_fails_closed_without_verified_files(tmp_path) -> None:
    with pytest.raises(EmbeddingUnavailable, match="missing"):
        LocalEmbeddingProvider(tmp_path)
    (tmp_path / "model.onnx").write_bytes(b"not a model")
    (tmp_path / "tokenizer.json").write_text("{}")
    with pytest.raises(EmbeddingUnavailable, match="checksum"):
        LocalEmbeddingProvider(tmp_path)


def test_tokenizer_requires_special_tokens() -> None:
    with pytest.raises(EmbeddingUnavailable):
        WordPieceTokenizer({"hello": 1})


@needs_model
def test_tokenizer_matches_reference_ids_and_truncates() -> None:
    tokenizer = WordPieceTokenizer.from_file(MODEL_DIR / "tokenizer.json")
    for text, expected in GOLDEN.items():
        assert tokenizer.encode(text) == expected
    assert len(tokenizer.encode("word " * 500)) == 128


@needs_model
def test_literal_special_tokens_in_text_are_not_control_tokens() -> None:
    tokenizer = WordPieceTokenizer.from_file(MODEL_DIR / "tokenizer.json")
    ids = tokenizer.encode("[SEP] [CLS]")
    # Only the framing tokens are control tokens; untrusted text cannot inject them.
    assert ids.count(101) == 1 and ids.count(102) == 1


@needs_model
@needs_runtime
async def test_embeddings_are_normalized_and_semantic() -> None:
    provider = LocalEmbeddingProvider(MODEL_DIR)
    vectors = await provider.embed(
        [
            "Low-volume breakouts on BTC reversed within an hour.",
            "Breakouts without volume failed quickly on Bitcoin.",
            "The weather in Santo Domingo is sunny today.",
        ]
    )
    assert all(len(vector) == EMBEDDING_DIMENSIONS for vector in vectors)

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert abs(cosine(vectors[0], vectors[0]) - 1) < 1e-4
    assert cosine(vectors[0], vectors[1]) > 0.6
    assert cosine(vectors[0], vectors[2]) < 0.2
    with pytest.raises(ValueError):
        await provider.embed([""])
    with pytest.raises(ValueError):
        await provider.embed(["x"] * 33)


async def test_null_vector_backend_is_explicitly_unavailable() -> None:
    backend = NullVectorBackend()
    assert backend.available is False
    await backend.upsert("m", [0.0] * EMBEDDING_DIMENSIONS, model="x")
    assert await backend.nearest([0.0] * EMBEDDING_DIMENSIONS, limit=5) == []


def test_vector_literal_is_validated() -> None:
    with pytest.raises(ValueError):
        _vector_literal([0.1, 0.2])
    with pytest.raises(ValueError):
        _vector_literal([float("nan")] * EMBEDDING_DIMENSIONS)
    assert _vector_literal([0.5] * EMBEDDING_DIMENSIONS).startswith("[0.5,")


POSTGRES_URL = os.getenv("HYVERION_TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL or not HAS_MODEL, reason="needs Postgres and the model")
async def test_pgvector_returns_semantic_neighbours_first() -> None:
    from trading_bot.db import Database
    from trading_bot.memory import MemoryType, SqlMemoryRepository, StrategicMemory
    from trading_bot.memory.vector import PgVectorBackend

    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    await database.initialize()
    backend = PgVectorBackend(database)
    await backend.ensure_schema()
    repository = SqlMemoryRepository(database)
    provider = LocalEmbeddingProvider(MODEL_DIR)
    now = datetime(2026, 9, 23, tzinfo=UTC)
    suffix = uuid4().hex[:8]
    texts = {
        f"brk-{suffix}": "Low-volume breakouts on BTC reverse quickly.",
        f"mr-{suffix}": "Mean reversion works on ETH in ranging markets.",
        f"wx-{suffix}": "Sunny weather in the Caribbean this week.",
    }
    vectors = await provider.embed(list(texts.values()))
    for (memory_id, body), vector in zip(texts.items(), vectors, strict=True):
        await repository.create_strategic(
            StrategicMemory(
                id=memory_id,
                knowledge_id=f"KNOW-{memory_id}",
                title=body[:40],
                summary=body,
                category=MemoryType.PATTERN,
                confidence=Decimal("0.5"),
                reliability=Decimal("0.5"),
                importance=Decimal("0.5"),
                valid_from=now,
                created_at=now,
                updated_at=now,
            ),
            content=body,
            created_by="test",
            change_reason="x",
        )
        await backend.upsert(memory_id, vector, model=provider.model)

    query = (await provider.embed(["Breakouts without volume fail on Bitcoin"]))[0]
    # The Docker database is shared across runs: rank our own rows exactly
    # instead of relying on a global top-N that other runs' rows can fill.
    scores = await backend.similarities(query, list(texts))
    ours = sorted(scores, key=lambda memory_id: -scores[memory_id])
    assert ours == [f"brk-{suffix}", f"mr-{suffix}", f"wx-{suffix}"]
    nearest = await backend.nearest(query, limit=10)
    assert [score for _, score in nearest] == sorted(
        (score for _, score in nearest), reverse=True
    )
    await database.close()
