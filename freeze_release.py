"""Plan cleanup and seal a paused benchmark; deletion is performed by the native PowerShell script."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sqlite3

from benchmark_store import atomic_json, now


def cleanup_plan(workspace, root):
    workspace, root = Path(workspace).resolve(), Path(root).resolve()
    data = workspace / 'data'
    db = sqlite3.connect(root / 'benchmark.sqlite'); db.row_factory = sqlite3.Row
    state = json.loads(db.execute("SELECT value FROM state WHERE key='pipeline'").fetchone()[0])
    if state.get('status') != 'blocked' or 'explicit user' not in state.get('reason', ''):
        raise ValueError('Stop the benchmark explicitly before planning deletion')
    if db.execute("SELECT count(*) FROM requests WHERE state='started'").fetchone()[0]:
        raise ValueError('Requests still in flight')
    samples = [dict(r) for r in db.execute('SELECT * FROM samples WHERE synthetic=0')]
    attempted = {r[0] for r in db.execute('SELECT DISTINCT j.sample_id FROM requests r JOIN jobs j ON j.id=r.job_id')}
    # Earlier REAL pilot attempts also qualify for retention; offline rendering alone does not.
    legacy_paths = set()
    for folder in (workspace / 'outputs').iterdir():
        if not folder.is_dir() or folder.resolve() == root or 'offline' in folder.name:
            continue
        results = folder / 'results.json'
        if results.exists():
            records = json.loads(results.read_text(encoding='utf-8'))
            if isinstance(records, list):
                for r in records:
                    if r.get('path') and (r.get('agents') or r.get('prediction') or r.get('error')):
                        legacy_paths.add(Path(r['path']).resolve())
    legacy_ids = {s['id'] for s in samples if Path(s['path']).resolve() in legacy_paths}
    retained = attempted | legacy_ids
    dropped = [s for s in samples if s['id'] not in retained]
    dropped_ids = {s['id'] for s in dropped}
    job_rows = [dict(r) for r in db.execute('SELECT id,sample_id,asset_id FROM jobs')]
    shared_assets = {r['asset_id'] for r in job_rows if r['sample_id'] not in dropped_ids}
    # Add assets of pilot-only retained inputs and all synthetic probes.
    sample_assets = {}
    sample_jobs = {}
    for row in job_rows:
        sample_jobs.setdefault(row['sample_id'], []).append(row['id'])
        sample_assets.setdefault(row['sample_id'], set()).add(row['asset_id'])
        if row['sample_id'] in retained or row['sample_id'].startswith('probe_'):
            shared_assets.add(row['asset_id'])
    targets = []
    membership = []
    for s in samples:
        source = Path(s['path']).resolve()
        if not source.is_relative_to(data) or not source.is_file():
            raise ValueError('Input is absent or outside data: ' + str(source))
        membership.append({'sample_id': s['id'], 'path': source.relative_to(workspace).as_posix(),
                           'sha256': s['sha'], 'retained': s['id'] in retained,
                           'reason': 'benchmark_attempt' if s['id'] in attempted else 'earlier_pilot_attempt' if s['id'] in legacy_ids else 'never_sent_for_inference'})
    for s in dropped:
        source = Path(s['path']).resolve()
        targets.append({'kind': 'model', 'path': str(source), 'sha256': s['sha'], 'bytes': source.stat().st_size, 'sample_id': s['id']})
        for aid in sample_assets.get(s['id'], set()) - shared_assets:
            folder = root / 'assets' / aid
            if folder.exists():
                files = [p for p in folder.rglob('*') if p.is_file()]
                targets.append({'kind': 'asset_directory', 'path': str(folder), 'file_count': len(files),
                                'bytes': sum(p.stat().st_size for p in files), 'sample_id': s['id']})
        for job_id in sample_jobs.get(s['id'], []):
            folder = root / 'jobs' / job_id
            if folder.exists():
                files = [p for p in folder.rglob('*') if p.is_file()]
                targets.append({'kind': 'job_directory', 'path': str(folder), 'file_count': len(files),
                                'bytes': sum(p.stat().st_size for p in files), 'sample_id': s['id']})
    db.close()
    # Ensure each target occurs only once, even with shared hashes.
    targets = list({t['path']: t for t in targets}.values())
    result = {'created': now(), 'workspace': str(workspace), 'root': str(root),
              'policy': 'Retain all attempted inputs, including failed and earlier real-pilot attempts; delete only never-attempted originals and their exclusive assets.',
              'original_models': len(samples), 'retained_models': sum(x['retained'] for x in membership),
              'removed_models': len(dropped), 'earlier_pilot_only_retained': len(legacy_ids - attempted),
              'target_count': len(targets), 'bytes': sum(t['bytes'] for t in targets), 'targets': targets}
    publication = workspace / 'outputs' / 'publication'; publication.mkdir(exist_ok=True)
    atomic_json(publication / 'cleanup_plan.json', result)
    atomic_json(publication / 'release_membership.json', membership)
    with (publication / 'release_membership.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(membership[0]));writer.writeheader();writer.writerows(membership)
    return {k: v for k, v in result.items() if k != 'targets'}


def seal(workspace, root):
    workspace, root = Path(workspace).resolve(), Path(root).resolve()
    publication = workspace / 'outputs' / 'publication'
    plan = json.loads((publication / 'cleanup_plan.json').read_text(encoding='utf-8'))
    if not (publication / 'cleanup_completed.json').exists():
        raise ValueError('Deletion receipt is missing')
    members = json.loads((publication / 'release_membership.json').read_text(encoding='utf-8'))
    db = sqlite3.connect(root / 'benchmark.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS release_membership(sample_id TEXT PRIMARY KEY, retained INTEGER, reason TEXT, relative_path TEXT)')
    db.executemany('INSERT OR REPLACE INTO release_membership VALUES(?,?,?,?)', [(m['sample_id'], int(m['retained']), m['reason'], m['path']) for m in members])
    record = {'sealed_at': now(), 'project': 'CAD Function Atlas', 'version': 'v0.1.0',
              'original_plan_models': plan['original_models'], 'retained_models': plan['retained_models'],
              'deleted_never_attempted_models': plan['removed_models'],
              'historical_pending_rows_preserved': True, 'resumable': False,
              'note': 'Paused at user request, then pruned. Historical unexecuted rows remain for honest completion accounting. Start a new root for future experiments.'}
    db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)', (now(), 'release_sealed_and_pruned', json.dumps(record)))
    db.commit(); db.close()
    atomic_json(root / 'FROZEN_RELEASE.json', record)
    return record


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['plan', 'seal'])
    p.add_argument('--workspace', type=Path, default=Path.cwd())
    p.add_argument('--root', type=Path, default=Path('outputs/full_benchmark_v1'))
    a = p.parse_args()
    print(json.dumps((cleanup_plan if a.command == 'plan' else seal)(a.workspace, a.root), ensure_ascii=True, indent=2))
