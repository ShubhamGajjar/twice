"""Group corrections that say the same thing into lessons.

Without Claude judgments, grouping uses TF-IDF cosine similarity over the
correction text (pure Python, no dependencies). With judgments, it groups the
one-line lessons Claude wrote, which is far more reliable because the same
rule is phrased the same way. Either way the output is a ranked list of
lessons: the ones repeated across the most sessions come first, because a
correction you made in two different sessions is the one that didn't stick.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePath

from twice.model import Turn

_STOPWORDS = frozenset(
    ["a", "about", "above", "after", "again", "all", "also", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "let", "me", "more", "most", "my", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours", "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "us", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours", "yourself", "please", "okay", "ok", "yeah", "yes", "like", "make", "sure", "want", "need", "going", "get", "got", "thing", "things", "one", "use", "used", "using"]
)
_TOKEN = re.compile(r"[a-z][a-z0-9_\-']+")


@dataclass
class Lesson:
    label: str
    turn_ids: list[str]
    sessions: int
    projects: list[str]
    first_seen: str
    last_seen: str
    examples: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.turn_ids)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "count": self.count,
            "sessions": self.sessions,
            "projects": self.projects,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "examples": self.examples,
            "turn_ids": self.turn_ids,
        }


def tokenize(text: str) -> list[str]:
    tokens = []
    for word in _TOKEN.findall(text.lower()):
        word = word.strip("'-")
        word = word.removesuffix("'s")
        if len(word) < 2 or word in _STOPWORDS:
            continue
        tokens.append(_stem(word))
    return tokens


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def tfidf(docs: list[list[str]]) -> list[dict[str, float]]:
    n = len(docs)
    df = Counter(term for doc in docs for term in set(doc))
    vectors = []
    for doc in docs:
        tf = Counter(doc)
        vec = {t: (c / len(doc)) * (math.log((1 + n) / (1 + df[t])) + 1) for t, c in tf.items()} if doc else {}
        vectors.append(_normalize(vec))
    return vectors


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def group(texts: list[str], threshold: float = 0.35) -> list[list[int]]:
    """Greedy single-pass clustering: each text joins the most similar group above threshold."""
    vectors = tfidf([tokenize(t) for t in texts])
    groups: list[list[int]] = []
    centroids: list[dict[str, float]] = []
    for i, vec in enumerate(vectors):
        if not vec:
            groups.append([i])
            centroids.append({})
            continue
        best, best_sim = -1, threshold
        for g, centroid in enumerate(centroids):
            sim = cosine(vec, centroid)
            if sim >= best_sim:
                best, best_sim = g, sim
        if best < 0:
            groups.append([i])
            centroids.append(dict(vec))
        else:
            groups[best].append(i)
            centroids[best] = _normalize(_add(centroids[best], vec, len(groups[best]) - 1))
    return groups


def build_lessons(
    turns: list[Turn],
    judgments: dict[str, dict] | None = None,
    threshold: float = 0.35,
    max_examples: int = 3,
) -> list[Lesson]:
    """Group candidate corrections into ranked lessons.

    With judgments, only turns Claude confirmed as corrections are kept and the
    lesson text is what gets grouped; otherwise every candidate is grouped on
    its own text.
    """
    if judgments:
        kept = [t for t in turns if judgments.get(t.id, {}).get("is_correction")]
        texts = [judgments[t.id].get("lesson") or t.text for t in kept]
    else:
        kept = [t for t in turns if t.is_candidate]
        texts = [t.text for t in kept]
    lessons = []
    for members in group(texts, threshold):
        member_turns = [kept[i] for i in members]
        member_texts = [texts[i] for i in members]
        stamps = sorted(t.timestamp for t in member_turns if t.timestamp)
        lessons.append(
            Lesson(
                label=_label(member_texts, use_text=bool(judgments)),
                turn_ids=[t.id for t in member_turns],
                sessions=len({t.session_id for t in member_turns}),
                projects=sorted({PurePath(t.project).name or t.project for t in member_turns if t.project}),
                first_seen=stamps[0] if stamps else "",
                last_seen=stamps[-1] if stamps else "",
                examples=[_clip(t.text) for t in member_turns[:max_examples]],
            )
        )
    lessons.sort(key=lambda l: (l.sessions, l.count, l.last_seen), reverse=True)
    return lessons


def _label(texts: list[str], use_text: bool) -> str:
    if use_text:
        # The most central lesson sentence names the group.
        if len(texts) == 1:
            return texts[0]
        vectors = tfidf([tokenize(t) for t in texts])
        centroid: dict[str, float] = {}
        for vec in vectors:
            centroid = _add(centroid, vec, 1)
        centroid = _normalize(centroid)
        best = max(range(len(texts)), key=lambda i: cosine(vectors[i], centroid))
        return texts[best]
    counts = Counter(term for t in texts for term in set(tokenize(t)))
    top = [term for term, _ in counts.most_common(4)]
    return "about: " + ", ".join(top) if top else _clip(texts[0], 80)


def _add(centroid: dict[str, float], vec: dict[str, float], weight: int) -> dict[str, float]:
    out = {k: v * weight for k, v in centroid.items()}
    for k, v in vec.items():
        out[k] = out.get(k, 0.0) + v
    return out


def _normalize(vec: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(v * v for v in vec.values()))
    return {k: v / norm for k, v in vec.items()} if norm else {}


def _clip(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."
