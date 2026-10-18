"""Sentence-transformers and deterministic fake embedding implementations."""

import hashlib
import os
from typing import Any, Optional, Sequence

import numpy as np

from dataProcessing.config import DATA_PROCESSING_DIR, load_config


class Embedder:
    """Dense, normalized embeddings produced by sentence-transformers."""

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        settings = config or load_config()
        self.model_name = str(settings["model_name"])
        self.max_seq_length = int(settings["max_seq_length"])
        self.batch_size = int(settings["batch_size"])

        cache_dir = DATA_PROCESSING_DIR / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(cache_dir)
        os.environ["HF_HUB_CACHE"] = str(cache_dir / "hub")
        os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(cache_dir)

        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name, cache_folder=str(cache_dir))
        self._model.max_seq_length = self.max_seq_length

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Encode texts as normalized dense vectors."""
        if not texts:
            dimension = self._model.get_sentence_embedding_dimension()
            return np.empty((0, int(dimension)), dtype=np.float32)
        vectors = self._model.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


class FakeEmbedder:
    """Deterministic hash-based normalized vectors for tests and dry runs."""

    def __init__(self, vector_dimension: int) -> None:
        if vector_dimension < 1:
            raise ValueError("vector_dimension must be at least 1.")
        self.vector_dimension = vector_dimension

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Return one stable unit vector per input text."""
        vectors = np.empty((len(texts), self.vector_dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            values: list[float] = []
            counter = 0
            while len(values) < self.vector_dimension:
                digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
                values.extend(
                    (int.from_bytes(digest[offset : offset + 4], "big") / 2147483647.5) - 1.0
                    for offset in range(0, len(digest), 4)
                )
                counter += 1
            vector = np.asarray(values[: self.vector_dimension], dtype=np.float32)
            norm = np.linalg.norm(vector)
            if norm == 0:
                vector[0] = 1
                norm = 1
            vectors[row] = vector / norm
        return vectors