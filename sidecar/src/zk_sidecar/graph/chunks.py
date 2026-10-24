"""Chunks: one per note, so atomic notes stay whole. Long documents split into about 1,200 tokens."""
import hashlib
from dataclasses import dataclass

from ..summarise import estimate_tokens, split_to_fit
from .corpus import Doc

CHUNK_TOKENS = 1200


@dataclass(frozen=True)
class Chunk:
    id: str
    doc: str
    n: int
    text: str

    @property
    def sha(self) -> str:
        """Extraction is cached by text, so an unchanged chunk is never read twice."""
        return hashlib.sha256(self.text.encode()).hexdigest()


def chunk(doc: Doc) -> list[Chunk]:
    parts = [doc.text] if estimate_tokens(doc.text) <= CHUNK_TOKENS else split_to_fit(doc.text, CHUNK_TOKENS)
    out = []
    for n, text in enumerate(parts):
        cid = hashlib.sha256(f'{doc.path}\0{n}\0{text}'.encode()).hexdigest()[:16]
        out.append(Chunk(cid, doc.path, n, text))
    return out
