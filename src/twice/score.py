"""Measure how well the miner (and the Claude judge) find real corrections.

Precision comes from labeled flagged turns. Recall needs labels on unflagged
turns too: the share of unflagged turns that are really corrections, times
how many unflagged turns there are, estimates what the miner missed. Every
rate comes with a 95% Wilson interval, because a few dozen labels leave real
uncertainty and the report should say so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from twice.model import Turn

SWEEP = (0.5, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5)


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class MinerMetrics:
    threshold: float
    population: int
    flagged: int
    labeled_flagged: int
    correct_flagged: int
    labeled_unflagged: int
    missed_unflagged: int

    @property
    def precision(self) -> float | None:
        return self.correct_flagged / self.labeled_flagged if self.labeled_flagged else None

    @property
    def miss_rate(self) -> float | None:
        return self.missed_unflagged / self.labeled_unflagged if self.labeled_unflagged else None

    @property
    def estimated_corrections(self) -> float | None:
        if self.precision is None or self.miss_rate is None:
            return None
        return self.flagged * self.precision + (self.population - self.flagged) * self.miss_rate

    @property
    def recall(self) -> float | None:
        total = self.estimated_corrections
        if total is None or self.precision is None:
            return None
        return (self.flagged * self.precision) / total if total else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p is not None and r is not None and (p + r) else None


def miner_metrics(turns: list[Turn], labels: dict[str, dict], threshold: float) -> MinerMetrics:
    by_id = {t.id: t for t in turns}
    flagged_labels, unflagged_labels = [], []
    for label in labels.values():
        turn = by_id.get(label["id"])
        if turn is None:
            continue  # labeled turn no longer in the mined set (e.g. filtered out)
        (flagged_labels if turn.score >= threshold else unflagged_labels).append(label)
    return MinerMetrics(
        threshold=threshold,
        population=len(turns),
        flagged=sum(1 for t in turns if t.score >= threshold),
        labeled_flagged=len(flagged_labels),
        correct_flagged=sum(1 for l in flagged_labels if l["is_correction"]),
        labeled_unflagged=len(unflagged_labels),
        missed_unflagged=sum(1 for l in unflagged_labels if l["is_correction"]),
    )


def threshold_sweep(turns: list[Turn], labels: dict[str, dict], base_threshold: float, thresholds=SWEEP) -> list[dict]:
    """Precision and recall at other thresholds, weighting each label back to its stratum.

    Strata are defined at `base_threshold`, the threshold the sample was drawn at.
    Returns [] when either stratum has no labels, because the weights would be undefined.
    """
    by_id = {t.id: t for t in turns}
    pairs = [(by_id[l["id"]], l["is_correction"]) for l in labels.values() if l["id"] in by_id]
    flagged_n = sum(1 for t in turns if t.score >= base_threshold)
    unflagged_n = len(turns) - flagged_n
    lab_flagged = sum(1 for t, _ in pairs if t.score >= base_threshold)
    lab_unflagged = len(pairs) - lab_flagged
    if not lab_flagged or not lab_unflagged:
        return []
    rows = []
    for t in thresholds:
        tp = fp = fn = 0.0
        for turn, is_correction in pairs:
            weight = flagged_n / lab_flagged if turn.score >= base_threshold else unflagged_n / lab_unflagged
            predicted = turn.score >= t
            if predicted and is_correction:
                tp += weight
            elif predicted:
                fp += weight
            elif is_correction:
                fn += weight
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        rows.append({"threshold": t, "precision": precision, "recall": recall})
    return rows


def judge_agreement(labels: dict[str, dict], judgments: dict[str, dict]) -> dict | None:
    """How often Claude's yes/no matches yours, on the turns you labeled."""
    pairs = [
        (bool(label["is_correction"]), bool(judgments[i].get("is_correction")))
        for i, label in labels.items()
        if i in judgments and judgments[i].get("status") == "ok"
    ]
    if not pairs:
        return None
    tp = sum(1 for h, j in pairs if h and j)
    fp = sum(1 for h, j in pairs if not h and j)
    fn = sum(1 for h, j in pairs if h and not j)
    tn = len(pairs) - tp - fp - fn
    n = len(pairs)
    observed = (tp + tn) / n
    expected = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else 1.0
    return {
        "n": n,
        "accuracy": observed,
        "accuracy_ci": wilson(tp + tn, n),
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "kappa": kappa,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def format_scores(m: MinerMetrics, sweep: list[dict], agreement: dict | None) -> str:
    lines = [
        f"Population: {m.population} messages you sent after the agent acted",
        f"Flagged at threshold {m.threshold}: {m.flagged}",
        "",
        "Miner",
    ]
    if m.precision is None:
        lines.append("  precision: no labeled flagged messages yet (run `twice label`)")
    else:
        lo, hi = wilson(m.correct_flagged, m.labeled_flagged)
        lines.append(f"  precision: {_pct(m.precision)}  (95% CI {_pct(lo)}-{_pct(hi)}, {m.labeled_flagged} labeled)")
    if m.miss_rate is None:
        lines.append("  recall:    needs labels on unflagged messages (twice label samples some by default)")
    else:
        lo, hi = wilson(m.missed_unflagged, m.labeled_unflagged)
        lines.append(f"  unflagged that were corrections: {_pct(m.miss_rate)}  (95% CI {_pct(lo)}-{_pct(hi)}, {m.labeled_unflagged} labeled)")
        if m.precision is None:
            lines.append("  recall:    needs labels on flagged messages too")
        elif m.recall is None:
            lines.append("  recall:    n/a (no corrections in the labeled sample yet)")
        else:
            lines.append(f"  recall:    {_pct(m.recall)}  (estimated)")
            lines.append(f"  F1:        {_pct(m.f1)}")
        if m.estimated_corrections is not None:
            lines.append(f"  estimated corrections in your sessions: {m.estimated_corrections:.0f}")
    if sweep:
        lines += ["", "  threshold  precision  recall"]
        for row in sweep:
            lines.append(f"  {row['threshold']:>9}  {_pct(row['precision']):>9}  {_pct(row['recall']):>6}")
    lines += ["", "Claude judge vs your labels"]
    if agreement is None:
        lines.append("  no overlap yet (run `twice judge` after labeling)")
    else:
        lo, hi = agreement["accuracy_ci"]
        lines.append(f"  agreement: {_pct(agreement['accuracy'])}  (95% CI {_pct(lo)}-{_pct(hi)}, {agreement['n']} labeled)")
        lines.append(f"  precision: {_pct(agreement['precision'])}   recall: {_pct(agreement['recall'])}   kappa: {agreement['kappa']:.2f}")
        lines.append("  (measured on your labeled sample, which over-represents flagged messages)")
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"
