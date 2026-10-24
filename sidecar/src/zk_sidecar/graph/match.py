"""Keep community identities across rebuilds.

Each new community is matched to an old one at the same level by the overlap of their phrases
(Jaccard), best pairs first. A matched community keeps its id, its note and your notes in it.
In the spike corpus, adding 10% more documents kept every top-level community matched.
"""
import secrets

MIN_JACCARD = 0.25
#: A report is rewritten only when membership moved more than this since it was written.
REPORT_KEEP = 0.8


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 1.0


def new_id() -> str:
    return 'c' + secrets.token_hex(4)


def match(new: list[tuple[int, set[str]]], old: list[tuple[str, int, set[str]]]) -> list[str | None]:
    """new: (level, members). old: (id, level, members). The old id for each new community, or None."""
    pairs = sorted(
        ((jaccard(nm, om), i, oid) for i, (nl, nm) in enumerate(new) for oid, ol, om in old if nl == ol),
        key=lambda p: (-p[0], p[1], p[2]),
    )
    out: list[str | None] = [None] * len(new)
    taken: set[str] = set()
    for score, i, oid in pairs:
        if score < MIN_JACCARD:
            break
        if out[i] is None and oid not in taken:
            out[i] = oid
            taken.add(oid)
    return out
