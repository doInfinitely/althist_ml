"""Embedding backends for the representation-mechanism analysis.

The paper uses Qwen3-Embedding-4B (2560-d, last-token pooling). That backend
is optional (``pip install althist[embeddings]``); a deterministic hashing
backend is provided for tests and dry runs.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np

QWEN3_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"


class EmbeddingBackend(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, d) array of L2-normalized embeddings."""
        ...


class SentenceTransformersBackend:
    """Qwen3-Embedding via sentence-transformers (paper Appendix C config)."""

    def __init__(self, model_name: str = QWEN3_EMBEDDING_MODEL, max_length: int = 512):
        import torch
        from sentence_transformers import SentenceTransformer

        # The checkpoint's native dtype is bf16, which pre-Ampere GPUs
        # (e.g. Titan RTX, Turing) only emulate — slow and numerically
        # riskier. Force fp16 there.
        kwargs = {}
        try:
            native_bf16 = torch.cuda.is_bf16_supported(including_emulation=False)
        except TypeError:  # older torch: no emulation distinction
            native_bf16 = torch.cuda.is_bf16_supported()
        if torch.cuda.is_available() and not native_bf16:
            kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
        self.model = SentenceTransformer(model_name, trust_remote_code=True, **kwargs)
        self.model.max_seq_length = max_length

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            self.model.encode(texts, batch_size=12, normalize_embeddings=True)
        )


class HashingBackend:
    """Deterministic char-ngram hashing embeddings for tests and dry runs.

    Similar texts get similar vectors (shared ngrams); no model download.
    """

    def __init__(self, dim: int = 256, ngram: int = 3):
        self.dim = dim
        self.ngram = ngram

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float64)
        for i, text in enumerate(texts):
            t = text.lower()
            for j in range(max(1, len(t) - self.ngram + 1)):
                gram = t[j : j + self.ngram]
                digest = hashlib.md5(gram.encode()).digest()
                idx = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                out[i, idx] += sign
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out


def make_backend(spec: str) -> EmbeddingBackend:
    """``qwen3`` | ``st:<model_name>`` | ``hashing``"""
    if spec == "qwen3":
        return SentenceTransformersBackend()
    if spec.startswith("st:"):
        return SentenceTransformersBackend(model_name=spec[3:])
    if spec == "hashing":
        return HashingBackend()
    raise ValueError(f"unknown embedding backend {spec!r}")
