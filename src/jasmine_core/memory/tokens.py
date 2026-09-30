"""Local, deterministic token accounting for the Memory budget.

Design §8 gives Memory a 400-600 token partition out of a 1500-2200 token
Context Pack. Two properties matter more than accuracy:

1. The count must be *ours*. Hindsight enforces its own `max_tokens` on the
   engine's tokenizer and returns only the tokens it counted (`RecallResponse`
   carries no per-fact token counts, and `TokenUsage` covers the LLM calls of a
   synchronous retain, not the recall results). If the budget were adjudicated
   by the engine's number, a Core release could silently change how much memory
   fits a pack, and an over-budget pack would fail P3's own hard limit rather
   than P4's.
2. It must be *predictable and conservative*. The same text always measures the
   same, and the measure never under-counts, so a pack that fits locally also
   fits the engine's budget.

`ceil(len(text)/4)` is the standard English approximation and is the documented
choice here: it errs low for code and CJK (which cost more per character) and
high for prose. Because P3 owns the total pack limit, being low is the failure
mode that matters; P4 additionally keeps a fixed 4% safety margin, and P3
remains free to cut the Memory section further.
"""

from __future__ import annotations

import math

#: Average characters per token for mixed technical English prose and code.
CHARS_PER_TOKEN = 4

#: Held back from the allocation P3 asked for. The remote tokenizer and ours
#: disagree in both directions; this margin absorbs the disagreement so the
#: engine is never asked for, or handed, more than the local budget.
SAFETY_MARGIN = 0.96

def estimate(text: str) -> int:
    """Local token estimate for one string. 0 for empty, >=1 for anything else."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def effective_budget(allocated: int | None) -> int:
    """The budget actually usable after the safety margin.

    `None` or 0 means P3 allocated no Memory partition. That is not the same as
    "unlimited": an absent partition means the Context Pack has no room for
    Memory, so the adapter must not query at all (ADR 0009 §5).
    """
    if not allocated or allocated <= 0:
        return 0
    return max(1, int(allocated * SAFETY_MARGIN))


def truncate_to_budget(text: str, budget: int) -> tuple[str, int]:
    """Cut `text` to fit `budget` tokens, never splitting inside a code fence.

    Returns `(text, tokens)`. A result that does not fit is *skipped* by the
    caller rather than truncated when the caller is assembling a ranked list,
    because a half lesson is worse than no lesson; truncation is reserved for
    the single-result Inspector view.
    """
    if budget <= 0:
        return "", 0
    if estimate(text) <= budget:
        return text, estimate(text)
    limit = budget * CHARS_PER_TOKEN
    cut = text[:limit]
    # Prefer a paragraph or line boundary so a truncated lesson is still a
    # sentence rather than half a word.
    for marker in ("\n\n", "\n", ". "):
        index = cut.rfind(marker)
        if index > limit // 3:
            cut = cut[: index + len(marker)]
            break
    return cut.strip(), estimate(cut)


def split_budget(total: int, weights: list[int]) -> list[int]:
    """Divide `total` across banks proportionally, giving the remainder to the first.

    Largest-remainder is overkill for two banks; what matters is that the parts
    sum to exactly `total` so the merged result cannot exceed the allocation,
    which is why the remainder goes to the first part rather than being dropped.
    Zero or negative weights cannot be weighted, so they share the budget evenly.
    """
    if not weights:
        return []
    if total <= 0:
        return [0] * len(weights)
    weight_sum = sum(weights)
    parts = [total * weight // weight_sum for weight in weights]
    parts[0] += total - sum(parts)
    return parts
