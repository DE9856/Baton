"""The two local models: an NLI classifier and a small sentence embedder, each loaded once on CPU.

If a model cannot be loaded the pipeline does not guess. `NLIUnavailable` scores everything as not supported, so
claims fall to Needs review, and the UI says the check did not run.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

NLI_MODEL = "cross-encoder/nli-deberta-v3-base"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass(frozen=True)
class NLIScore:
    entailment: float
    contradiction: float
    neutral: float


class NLI:
    available = True

    def __init__(self, model_name: str = NLI_MODEL, threads: int = 2, batch_size: int = 16):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        torch.set_num_threads(threads)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name).eval()
        labels = {v.lower(): k for k, v in self.model.config.id2label.items()}
        self.i_ent, self.i_con, self.i_neu = labels["entailment"], labels["contradiction"], labels["neutral"]
        self.batch_size = batch_size
        self.cache: dict[tuple[str, str], NLIScore] = {}

    def score(self, pairs: list[tuple[str, str]]) -> list[NLIScore]:
        todo = list({p for p in pairs if p not in self.cache})
        todo.sort(key=lambda p: len(p[0]) + len(p[1]))  # similar lengths batch together, which is faster on CPU
        for i in range(0, len(todo), self.batch_size):
            batch = todo[i:i + self.batch_size]
            enc = self.tokenizer([p[0] for p in batch], [p[1] for p in batch], padding=True, truncation=True,
                                 max_length=512, return_tensors="pt")
            with self.torch.inference_mode():
                probs = self.torch.softmax(self.model(**enc).logits, dim=-1).tolist()
            for pair, row in zip(batch, probs):
                self.cache[pair] = NLIScore(row[self.i_ent], row[self.i_con], row[self.i_neu])
        return [self.cache[p] for p in pairs]


class NLIUnavailable:
    available = False

    def score(self, pairs: list[tuple[str, str]]) -> list[NLIScore]:
        return [NLIScore(0.0, 0.0, 1.0) for _ in pairs]


class Embedder:
    available = True

    def __init__(self, model_name: str = EMBED_MODEL):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name).eval()

    def embed(self, texts: list[str]):
        """Mean-pooled, L2-normalised sentence vectors: the published usage of this model."""
        vectors = []
        for i in range(0, len(texts), 32):
            enc = self.tokenizer(texts[i:i + 32], padding=True, truncation=True, max_length=256, return_tensors="pt")
            with self.torch.inference_mode():
                out = self.model(**enc).last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).float()
            pooled = (out * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            vectors.append(self.torch.nn.functional.normalize(pooled, dim=1))
        return self.torch.cat(vectors).numpy() if vectors else None


_lock = threading.Lock()
_nli = None
_embedder = None


def get_nli():
    global _nli
    with _lock:
        if _nli is None:
            try:
                _nli = NLI()
            except Exception:
                log.exception("NLI model could not be loaded; claims will stay in Needs review")
                _nli = NLIUnavailable()
        return _nli


def get_embedder():
    global _embedder
    with _lock:
        if _embedder is None:
            try:
                _embedder = Embedder()
            except Exception:
                log.exception("Embedding model could not be loaded; conflict candidates fall back to word overlap")
                _embedder = False
        return _embedder or None
