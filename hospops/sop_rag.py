"""Hand-rolled TF-IDF retrieval over the hospital SOP markdown files.

Chunks are split on ``##`` headers; every hit cites ``<file> §<section>``.
When nothing relevant is found the assistant says so explicitly instead of
improvising policy. SOPs here are operational (flow, beds, discharge,
escalation) -- never clinical guidance.
"""

import math
import os
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HEADER_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def _tokenize(text):
    return _TOKEN_RE.findall(text.lower())


def load_chunks(sops_dir):
    """Split each SOP markdown file into {source, section, text} chunks."""
    chunks = []
    for fname in sorted(os.listdir(sops_dir)):
        if not fname.endswith(".md"):
            continue
        with open(os.path.join(sops_dir, fname), encoding="utf-8") as fh:
            body = fh.read()
        parts = _HEADER_RE.split(body)
        intro = parts[0].strip()
        # Skip title-only intros ("# SOP-01 ..."): they add noise tokens that
        # cause false-positive matches on otherwise unrelated queries.
        if intro and not intro.lstrip().startswith("#"):
            chunks.append({"source": fname, "section": "overview", "text": intro})
        for section, text in zip(parts[1::2], parts[2::2]):
            text = text.strip()
            if text:
                chunks.append({"source": fname, "section": section.strip(), "text": text})
    return chunks


class SopIndex:
    def __init__(self, chunks):
        self.chunks = chunks
        self._doc_tf = [Counter(_tokenize(c["text"])) for c in chunks]
        df = Counter()
        for tf in self._doc_tf:
            for term in tf:
                df[term] += 1
        n = max(1, len(chunks))
        self._idf = {t: math.log(n / f) for t, f in df.items()}

    def _vector(self, tf):
        return {t: (1 + math.log(f)) * self._idf[t] for t, f in tf.items()
                if t in self._idf}

    @staticmethod
    def _cosine(a, b):
        if not a or not b:
            return 0.0
        dot = sum(a[t] * b[t] for t in a if t in b)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def retrieve(self, query, k=3):
        qvec = self._vector(Counter(_tokenize(query)))
        scored = []
        for chunk, tf in zip(self.chunks, self._doc_tf):
            score = self._cosine(qvec, self._vector(tf))
            scored.append({
                "score": round(score, 4),
                "source": chunk["source"],
                "section": chunk["section"],
                "text": chunk["text"],
                "citation": f"{chunk['source']} §{chunk['section']}",
            })
        scored.sort(key=lambda h: h["score"], reverse=True)
        return scored[:k]

    def answer(self, query, k=3, min_score=0.04):
        """Cited excerpts, or an explicit could-not-find response."""
        hits = [h for h in self.retrieve(query, k) if h["score"] >= min_score]
        if not hits:
            return {
                "found": False,
                "message": "I could not find this in the hospital SOPs.",
                "citations": [],
                "excerpts": [],
            }
        return {
            "found": True,
            "message": "Per the hospital SOPs:",
            "citations": [h["citation"] for h in hits],
            "excerpts": [
                {"citation": h["citation"], "text": h["text"][:400]} for h in hits
            ],
        }
