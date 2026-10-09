"""Compare GraphRAG index runs: time, LLM calls, graph size, communities, and how well they separate the corpus."""
import json, sys
from collections import Counter
from pathlib import Path
import pandas as pd

DOMAIN = {}
for r in json.load(open('convert.json')):
    DOMAIN[r['slug']] = 'papers' if r['file'].startswith('papers') else 'maker' if r['file'].startswith('maker') else 'recipes'


def run(root):
    out = Path(root) / 'output'
    stats = json.load(open(out / 'stats.json'))
    calls = {d.name: len(list(d.iterdir())) for d in (Path(root) / 'cache').iterdir() if d.is_dir()}
    docs = pd.read_parquet(out / 'documents.parquet')
    units = pd.read_parquet(out / 'text_units.parquet')
    ents = pd.read_parquet(out / 'entities.parquet')
    rels = pd.read_parquet(out / 'relationships.parquet')
    comms = pd.read_parquet(out / 'communities.parquet')
    reps = pd.read_parquet(out / 'community_reports.parquet')
    doc_domain = {r.id: DOMAIN.get(Path(r.title).stem, '?') for r in docs.itertuples()}
    unit_domain = {r.id: doc_domain.get(r.document_id, '?') for r in units.itertuples()}
    # Purity: share of a community's text units that come from its main domain.
    pur = []
    for c in comms.itertuples():
        doms = Counter(unit_domain.get(u, '?') for u in (c.text_unit_ids if c.text_unit_ids is not None else []))
        if doms: pur.append((c.level, doms.most_common(1)[0][1] / sum(doms.values()), len(c.entity_ids)))
    p = pd.DataFrame(pur, columns=['level', 'purity', 'size'])
    return {
        'minutes': round(stats['total_runtime'] / 60, 1),
        'by_workflow_min': {k: round(v['overall'] / 60, 1) for k, v in stats['workflows'].items() if v['overall'] > 30},
        'llm_calls (cached)': calls,
        'chunks': len(units), 'entities': len(ents), 'relationships': len(rels),
        'entity_types': dict(Counter(ents['type']).most_common(8)),
        'communities_per_level': dict(Counter(comms['level']).items()),
        'mean_purity_by_level': p.groupby('level')['purity'].mean().round(3).to_dict(),
        'top_level_titles': reps[reps.level == 0].sort_values('rank', ascending=False)['title'].head(12).tolist(),
    }


if __name__ == '__main__':
    print(json.dumps({r: run(r) for r in sys.argv[1:]}, indent=1, default=str))
