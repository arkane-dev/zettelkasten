"""Typed entities with GLiNER, a small zero-shot NER model, for a GraphRAG run's text units.

Noun phrases from --method fast have no type. Here GLiNER tags each chunk with the types we
want as notes, and nothing else: no people, no quantities. Entities are keyed by text unit,
so they can be joined to the fast graph's communities.

Writes <root>/gliner/entities.parquet (title, type, frequency, text_unit_ids, score)
and prints a summary.

Usage: python gliner_entities.py <root>
"""
import re, sys, time
from collections import defaultdict
from pathlib import Path

import pandas as pd
import torch
from gliner import GLiNER

MODEL = 'urchade/gliner_medium-v2.1'
LABELS = ['concept', 'method', 'dataset', 'tool', 'material', 'ingredient']
THRESHOLD = 0.5
WINDOW_WORDS = 200  # GLiNER reads at most 384 tokens. Windows of whole sentences stay under that.


def windows(text):
    """Split a chunk into windows of whole sentences, about WINDOW_WORDS words each."""
    out, cur, n = [], [], 0
    sents = []
    for s in re.split(r'(?<=[.!?])\s+|\n{2,}', text):
        words = s.split()  # tables and lists have no sentence breaks: hard-split those
        sents += [' '.join(words[i:i + WINDOW_WORDS]) for i in range(0, len(words), WINDOW_WORDS)]
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


def norm(t):
    return re.sub(r'\s+', ' ', t).strip().strip('.,;:()[]"\'').lower()


root = Path(sys.argv[1])
units = pd.read_parquet(root / 'output' / 'text_units.parquet')
model = GLiNER.from_pretrained(MODEL).to('cuda' if torch.cuda.is_available() else 'cpu')

texts, owner = [], []
for u in units.itertuples():
    for w in windows(u.text):
        texts.append(w)
        owner.append(u.id)

t0 = time.time()
preds = model.inference(texts, LABELS, threshold=THRESHOLD, batch_size=8)
secs = time.time() - t0

found = defaultdict(lambda: {'types': defaultdict(int), 'units': set(), 'scores': []})
for uid, ents in zip(owner, preds):
    for e in ents:
        k = norm(e['text'])
        if len(k) < 2:
            continue
        found[k]['types'][e['label']] += 1
        found[k]['units'].add(uid)
        found[k]['scores'].append(e['score'])

rows = [{
    'title': k,
    'type': max(v['types'], key=v['types'].get),  # majority label across mentions
    'frequency': len(v['units']),
    'mentions': sum(v['types'].values()),
    'text_unit_ids': sorted(v['units']),
    'score': round(sum(v['scores']) / len(v['scores']), 3),
} for k, v in found.items()]
df = pd.DataFrame(rows).sort_values('frequency', ascending=False)
(root / 'gliner').mkdir(exist_ok=True)
df.to_parquet(root / 'gliner' / 'entities.parquet')

print(f'{len(units)} chunks, {len(texts)} windows, {secs:.0f} s on {model.device}')
print(f'{len(df)} entities, {int(df.mentions.sum())} mentions')
print('by type:', df.type.value_counts().to_dict())
for t in LABELS:
    print(f'  {t:10}', ', '.join(df[df.type == t].title.head(12)))
