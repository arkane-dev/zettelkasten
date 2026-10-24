"""Find what each chunk is about.

Noun phrases build the graph. They're what GraphRAG's fast method uses: TextBlob's fast
extractor with the same filters. Typed entities from GLiNER, a small NER model, name each
community's key ideas. GLiNER is optional (`pip install zk-sidecar[entities]`).
"""
import re
import threading
from collections.abc import Callable
from pathlib import Path

NLTK_DATA = ('brown', 'punkt', 'punkt_tab', 'averaged_perceptron_tagger_eng')
MAX_WORD = 15

#: Words that never make a phrase useful. GraphRAG's list, which it compares the wrong way round,
#: plus page furniture from papers and filler that slipped through in the spike.
STOP_WORDS = {
    'STUFF', 'THING', 'THINGS', 'BUNCH', 'BIT', 'BITS', 'PEOPLE', 'PERSON', 'OKAY', 'HEY', 'HI', 'HELLO',
    'LAUGHTER', 'OH', 'FIGURE', 'FIG', 'TABLE', 'APPENDIX', 'PROCEEDINGS', 'PREPRINT', 'ARXIV', 'ICLR',
    'NEURIPS', 'ICML', 'CVPR', 'ACL', 'EMNLP', 'AAAI', 'IEEE', 'ACM', 'ET', 'AL', 'ETC', 'EG', 'IE',
}
STOP_PHRASES = {
    'CONFERENCE PAPER', 'GOOD IDEA', 'OUR METHOD', 'OUR APPROACH', 'OUR WORK', 'THIS PAPER', 'THIS WORK',
    'SUPPLEMENTARY MATERIAL', 'RELATED WORK', 'FUTURE WORK', 'ET AL', 'PRINT', 'PAGE',
}
VALID = re.compile(r'^[A-Za-z0-9\-]+$')
_TRAINED = None


class NounPhrases:
    #: Part of the cache key. Change it when the filters change.
    name = 'np1'

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._ready = False
        self._lock = threading.Lock()

    def _setup(self) -> None:
        with self._lock:
            if self._ready:
                return
            import nltk

            self.data_dir.mkdir(parents=True, exist_ok=True)
            if str(self.data_dir) not in nltk.data.path:
                nltk.data.path.insert(0, str(self.data_dir))
            for pkg in NLTK_DATA:
                found = any((Path(p) / sub / pkg).exists() or (Path(p) / sub / f'{pkg}.zip').exists()
                            for p in nltk.data.path for sub in ('corpora', 'tokenizers', 'taggers'))
                if not found and not nltk.download(pkg, download_dir=str(self.data_dir), quiet=True):
                    raise RuntimeError(f'Could not download the NLTK package {pkg}. Check the network.')
            global _TRAINED
            if _TRAINED is None:
                from textblob.np_extractors import FastNPExtractor

                _TRAINED = FastNPExtractor()
                _TRAINED.train()  # about 3 s on the Brown corpus, so once per process
            self._np = _TRAINED
            self._ready = True

    def extract(self, text: str) -> list[str]:
        self._setup()
        from textblob import TextBlob

        blob = TextBlob(text, np_extractor=self._np)
        proper = {w.upper() for w, tag in blob.tags if tag == 'NNP'}
        out = set()
        for phrase in blob.noun_phrases:
            tokens = [t for t in phrase.split() if t.upper() not in STOP_WORDS]
            if not tokens or not all(VALID.match(t) and len(t) <= MAX_WORD for t in tokens):
                continue
            text_ = ' '.join(tokens).upper()
            if text_ in STOP_PHRASES or text_.isdigit():
                continue
            compound = any('-' in t and len(t.split('-')) > 1 and len(t) > 1 for t in tokens)
            if len(tokens) > 1 or compound or any(t.upper() in proper for t in tokens):
                out.add(text_)
        return sorted(out)


#: Types we want as key ideas. No people (mostly citation authors) and no quantities.
LABELS = ['concept', 'method', 'dataset', 'tool', 'software', 'AI model', 'material', 'ingredient']
MODEL = 'urchade/gliner_medium-v2.1'
THRESHOLD = 0.5
WINDOW_WORDS = 150  # GLiNER reads at most 384 tokens, and technical words split into several


def gliner_available() -> bool:
    import importlib.util

    return importlib.util.find_spec('gliner') is not None and importlib.util.find_spec('torch') is not None


def windows(text: str) -> list[str]:
    """Windows of whole sentences, about WINDOW_WORDS words each."""
    sents: list[str] = []
    for s in re.split(r'(?<=[.!?])\s+|\n{2,}', text):
        words = s.split()
        sents += [' '.join(words[i:i + WINDOW_WORDS]) for i in range(0, len(words), WINDOW_WORDS)]
    out, cur, n = [], [], 0
    for s in sents:
        w = len(s.split())
        if cur and n + w > WINDOW_WORDS:
            out.append(' '.join(cur))
            cur, n = [], 0
        cur.append(s)
        n += w
    if cur:
        out.append(' '.join(cur))
    return out


def norm_entity(t: str) -> str:
    return re.sub(r'\s+', ' ', t).strip().strip('.,;:()[]"\'').lower()


class Entities:
    name = 'gliner2'

    def __init__(self):
        self._model = None

    def extract_many(self, texts: list[str], progress: Callable[[int, int], None] = lambda i, n: None
                     ) -> list[list[tuple[str, str]]]:
        """(entity, type) pairs for each text. Loads the model for this call only, then frees it."""
        import warnings

        import torch
        from gliner import GLiNER

        model = GLiNER.from_pretrained(MODEL).to('cuda' if torch.cuda.is_available() else 'cpu')
        try:
            out: list[list[tuple[str, str]]] = []
            for i, text in enumerate(texts):
                found: dict[str, str] = {}
                wins = windows(text)
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')  # a truncation note per long window floods the log
                    preds = model.inference(wins, LABELS, threshold=THRESHOLD, batch_size=8) if wins else []
                for ents in preds:
                    for e in ents:
                        k = norm_entity(e['text'])
                        if len(k) >= 2 and k.upper() not in STOP_PHRASES and k.upper() not in STOP_WORDS:
                            found.setdefault(k, e['label'])
                out.append(sorted(found.items()))
                progress(i + 1, len(texts))
            return out
        finally:
            del model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
