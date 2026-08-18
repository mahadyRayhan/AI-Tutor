# backend/app/core/concept_canon.py
"""
Canonical concept naming for the mastery store.

Different code paths historically wrote `user_knowledge` rows under inconsistent
concept spellings — "variables", "variable", "Variables", "Variables and Types" —
because the quiz and code-review paths never normalised the topic name, while the
dashboard reads the canonical coarse topic ("Variables"). Evidence therefore
scattered across several rows, and the dashboard (reading only the canonical name)
showed none of it — the "quiz certified in chat but the dashboard says 0" bug.

`canonical_concept()` folds the known case / plural / whitespace variants — and the
well-known finer curriculum sub-topics — onto the nine coarse dashboard topics, so
every read and write for the same topic lands on ONE row.

It is deliberately CONSERVATIVE: a concept whose normalised token-set is not a known
variant is returned UNCHANGED (never title-cased or guessed), so unrelated concepts
and graph node names are never merged or relabelled by accident.
"""
import re

# The nine coarse topics the dashboard renders (must match main.SKILL_TOPICS).
CANONICAL_TOPICS = [
    "Variables", "Control Flow", "Functions", "Arrays", "Strings",
    "Pointers", "Structures", "Memory Allocation", "File I/O",
]

# Filler tokens dropped before comparing (mirrors user_knowledge_manager).
_FILLER = {"and", "the", "of", "to", "a", "an", "in", "with", "for", "using"}


def _tokens(s: str) -> frozenset:
    """Lowercase, strip punctuation, singularise, drop filler → a token set.
    'Variables' → {'variable'}; 'Variables and Types' → {'variable','type'}."""
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    out = []
    for t in s.split():
        if t in _FILLER:
            continue
        if t.endswith("s") and len(t) > 3:   # arrays→array, pointers→pointer
            t = t[:-1]
        out.append(t)
    return frozenset(out)


# token-set → canonical coarse-topic name.
#
# Scope is deliberately narrow: ONLY case / plural / whitespace variants of the nine
# coarse topics themselves fold together ("variables" / "variable" / "Variables" →
# "Variables"). Finer curriculum nodes ("Variables and Types", "Loops", "malloc") are
# NOT folded into a coarse bucket — that would silently rewrite how existing
# fine-grained rows (including the evaluation cohorts) are read. Folding fine→coarse
# is a separate, migration-gated design decision, intentionally out of scope here.
_REGISTRY = {_tokens(_t): _t for _t in CANONICAL_TOPICS}


def canonical_concept(name: str) -> str:
    """Return the canonical coarse-topic name for `name`, or `name` unchanged when
    it is not a recognised variant (matched by normalised token-set)."""
    if not name:
        return name
    return _REGISTRY.get(_tokens(name), name)


def is_canonical_topic(name: str) -> bool:
    """True if `name` is (a variant of) one of the nine coarse dashboard topics."""
    return _tokens(name) in _REGISTRY
