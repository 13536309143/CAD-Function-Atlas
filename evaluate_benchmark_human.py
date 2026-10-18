"""Evaluate explicit human functional labels without changing inference outputs."""
import argparse
import csv
import json
from pathlib import Path
from collections import Counter

from benchmark_store import connect, atomic_json, now
from cad_functions import FUNCTIONS


def evaluate_human(root, labels_file):
    root = Path(root)
    with Path(labels_file).open(encoding='utf-8-sig', newline='') as stream:
        annotations = list(csv.DictReader(stream))
    db = connect(root)
    jobs = {r['sample_id']: dict(r) for r in db.execute("SELECT sample_id,state,payload FROM jobs WHERE experiment='full_multi'")}
    db.close()
    labels, seen = {}, set()
    for row in annotations:
        sid = row['sample_id']
        if sid not in jobs or sid in seen:
            raise ValueError(f'Unknown or duplicate human sample ID: {sid}')
        seen.add(sid)
        code = row.get('confirmed_function_code', '').strip()
        if not code:
            continue
        if code not in FUNCTIONS:
            raise ValueError(f'Invalid confirmed function code for {sid}: {code}')
        labels[sid] = {'code': code, 'note': row.get('reviewer_note', '')}
    if not labels:
        raise ValueError('No explicit human labels provided; blank review rows are not ground truth')
    evaluated, confusion, class_counts = [], Counter(), Counter()
    for sid, label in labels.items():
        job = jobs[sid]
        if job['state'] not in {'done', 'error'}:
            continue
        result = json.loads(job['payload']) if job['payload'] else {}
        prediction = result.get('prediction') or {}
        actual = prediction.get('function_code', 'error')
        evaluated.append({'sample_id': sid, 'human_function': label['code'], 'prediction': actual,
                          'matched': actual == label['code'], 'status': result.get('status', 'error')})
        confusion[(label['code'], actual)] += 1
        class_counts[label['code']] += 1
    accepted = [r for r in evaluated if r['status'] == 'accepted']
    true_unknown = [r for r in evaluated if r['human_function'] == 'unknown']
    predicted_unknown = [r for r in evaluated if r['prediction'] == 'unknown']
    div = lambda a, b: a / b if b else None
    report = {'at': now(), 'labels_file': str(Path(labels_file).resolve()), 'n_labels': len(labels),
              'n_evaluated_terminal': len(evaluated), 'n_labelled_pending': len(labels) - len(evaluated),
              'human_label_coverage_all_sources': len(labels) / len(jobs),
              'human_accuracy_on_labelled_terminal_subset': div(sum(r['matched'] for r in evaluated), len(evaluated)),
              'accepted_human_accuracy_on_labelled_subset': div(sum(r['matched'] for r in accepted), len(accepted)),
              'unknown_recall_on_labelled_subset': div(sum(r['prediction'] == 'unknown' for r in true_unknown), len(true_unknown)),
              'unknown_precision_on_labelled_subset': div(sum(r['human_function'] == 'unknown' for r in predicted_unknown), len(predicted_unknown)),
              'class_counts': dict(class_counts),
              'confusion': [{'human': a, 'predicted': b, 'n': n} for (a, b), n in confusion.items()],
              'cases': evaluated, 'human_labels': labels,
              'warning': 'Only explicit human-labelled terminal cases are scored; selection bias and annotation quality must be assessed. Original predictions stay unchanged.'}
    atomic_json(root / 'human_evaluation.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_human(args.root, args.labels)
    print(json.dumps(result, ensure_ascii=True, indent=2))
