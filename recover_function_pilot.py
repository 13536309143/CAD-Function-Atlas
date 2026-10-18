"""Resume schema-failed samples into a NEW report, reusing completed agent calls."""
import argparse
import json
from pathlib import Path
import shutil
import sys

from cad_agents import create_vision_client as MiniMaxVision
from cad_geometry import prepare_mesh
from run_function_pilot import classify_function, copy_grouped_models, save_function_report
from run_pilot import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    source, target = args.run.resolve(), args.out.resolve()
    if target == source or source in target.parents:
        parser.error('Recovery output must be new and outside its source run')
    metadata = json.loads((source / 'run.json').read_text(encoding='utf-8'))
    records = json.loads((source / 'results.json').read_text(encoding='utf-8'))
    if metadata.get('task') != 'functional_classification_only':
        parser.error('Only functional classification runs can be resumed')
    client = MiniMaxVision(metadata['max_calls'])
    client.calls = metadata['api_calls']
    before = client.calls
    # Original run and its failed responses remain unchanged.
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('organized_models'))
    write_json(target / 'results_before_schema_repair.json', records)
    metadata['recovery_of'] = str(source)
    metadata['initial_api_calls'] = before
    metadata.pop('copy_error', None)
    for record in records:
        record.pop('organized_path', None)
        if record['status'] != 'error':
            continue
        print(record['sample_id'] + ': resuming incomplete function result', flush=True)
        folder = target / record['sample_id']
        try:
            if not (folder / 'geometry.json').exists():
                raise ValueError('Geometry preprocessing failed; schema recovery cannot repair geometry')
            geometry = json.loads((folder / 'geometry.json').read_text(encoding='utf-8'))
            mesh, _, _ = prepare_mesh(Path(record['path']))
            update = classify_function(client, mesh, geometry, folder / 'views.jpg', folder,
                                       metadata['threshold'], reuse_audits=True)
            record['previous_error'] = record.pop('error', '')
            record.update(update)
        except Exception as exc:
            record['error'] = client.redact(exc)
            print('  ' + record['error'], flush=True)
    metadata['api_calls'] = client.calls
    metadata['schema_recovery_api_calls'] = client.calls - before
    try:
        metadata['copied_models'] = len(copy_grouped_models(records, target))
    except Exception as exc:
        metadata['copy_error'] = client.redact(exc)
    metrics = save_function_report(target, records, metadata)
    print(json.dumps({'output': str(target), 'additional_calls': client.calls - before,
                      'cumulative_calls': client.calls, 'metrics': metrics}, ensure_ascii=False, indent=2), flush=True)
    return int(bool(metadata.get('copy_error') or any(r['status'] == 'error' for r in records)))


if __name__ == '__main__':
    sys.exit(main())
