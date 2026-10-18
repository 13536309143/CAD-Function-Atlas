"""Evaluate against explicit human labels without rewriting original predictions."""
import argparse
import json
from pathlib import Path

from cad_eval import evaluate


def review(run: Path, labels_path: Path):
    records = json.loads((run / 'results.json').read_text(encoding='utf-8'))
    labels = json.loads(labels_path.read_text(encoding='utf-8'))
    expected = {r['sample_id'] for r in records}
    seen = set()
    confirmations = {}
    for item in labels:
        sid = item['sample_id']
        if sid not in expected or sid in seen:
            raise ValueError(f'Unknown or duplicate sample ID: {sid}')
        seen.add(sid)
        label = item.get('confirmed_label')
        if label is not None:
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f'Invalid human label: {sid}')
            confirmations[sid] = {'label': label.strip(), 'note': item.get('reviewer_note', '')}
    if not confirmations:
        raise ValueError('No confirmed human labels; fill confirmed_label in a copy of review_queue.json')
    reviewed = [dict(r, ground_truth=confirmations[r['sample_id']]['label']) for r in records if r['sample_id'] in confirmations]
    return {'n_human_reviewed': len(reviewed), 'n_total': len(records),
            'human_review_coverage': len(reviewed) / len(records),
            'labels': confirmations, 'metrics_on_human_reviewed_subset': evaluate(reviewed),
            'note': 'Metrics apply only to the human-reviewed subset. Original predictions and directory labels remain unchanged.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    args = parser.parse_args()
    result = review(args.run, args.labels)
    target = args.run / 'human_validation.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Human validation saved to {target}; original predictions preserved.')
