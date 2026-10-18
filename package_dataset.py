"""Build a portable, credential-free Hugging Face dataset from the sealed local release."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import zipfile

from dotenv import dotenv_values


WORKSPACE = Path(__file__).resolve().parent


def portable(value):
    if isinstance(value, str):
        prefix = str(WORKSPACE)
        text = value.replace(prefix + '\\', '').replace(prefix + '/', '')
        text = text.replace(prefix.replace('\\', '/') + '/', '')
        text = text.replace(prefix.replace('\\', '\\\\') + '\\\\', '')
        return text
    if isinstance(value, list):
        return [portable(x) for x in value]
    if isinstance(value, dict):
        return {k: portable(v) for k, v in value.items()}
    return value


def secrets():
    values = [v for k, v in dotenv_values(WORKSPACE / '.env').items() if v and len(v) >= 16 and any(x in k.upper() for x in ('KEY', 'TOKEN'))]
    token = Path.home() / '.cache' / 'huggingface' / 'token'
    if token.exists():
        values.append(token.read_text(encoding='utf-8').strip())
    return [v for v in values if len(v) >= 16]


def clean_text(text, known_secrets):
    for secret in known_secrets:
        if secret in text:
            raise ValueError('Credential detected; publication stopped')
    return portable(text)


def write_json(path, value, known_secrets):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clean_text(json.dumps(portable(value), ensure_ascii=False, indent=2), known_secrets), encoding='utf-8')


def archive_files(entries, folder, prefix, known_secrets, max_bytes=512 * 1024**2):
    folder.mkdir(parents=True, exist_ok=True)
    result, archive, size, index = [], None, 0, 0
    try:
        for source, name in entries:
            source = Path(source)
            if archive is None or (size and size + source.stat().st_size > max_bytes):
                if archive:
                    archive.close()
                path = folder / f'{prefix}-{index:05d}.zip'
                archive = zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True)
                result.append(path);index += 1;size = 0
            if source.suffix.lower() in {'.json', '.jsonl', '.csv', '.md', '.txt', '.py', '.ps1', '.log', '.yml', '.yaml', '.html'}:
                text = source.read_text(encoding='utf-8-sig')
                archive.writestr(name, clean_text(text, known_secrets).encode('utf-8'))
            else:
                archive.write(source, name)
            size += source.stat().st_size
    finally:
        if archive:
            archive.close()
    return result


def build(root, out):
    root, out = Path(root).resolve(), Path(out).resolve()
    if not (root / 'FROZEN_RELEASE.json').exists():
        raise ValueError('Only a sealed release can be packaged')
    if out.exists():
        raise ValueError('Use a new package directory; do not mix release contents')
    out.mkdir(parents=True)
    for name in ['records', 'benchmark', 'reports', 'audit']:
        (out / name).mkdir()
    known = secrets()
    source = sqlite3.connect(root / 'benchmark.sqlite');source.row_factory = sqlite3.Row
    published = sqlite3.connect(out / 'benchmark' / 'benchmark.sqlite')
    source.backup(published)
    members = [dict(r) for r in source.execute('SELECT * FROM release_membership ORDER BY sample_id')]
    member_map = {r['sample_id']: r for r in members}
    all_samples = [dict(r) for r in source.execute('SELECT * FROM samples')]
    jobs = [dict(r) for r in source.execute('SELECT * FROM jobs ORDER BY id')]
    main = {r['sample_id']: r for r in jobs if r['experiment'] == 'full_multi'}
    sample_rows = []
    for s in all_samples:
        member = member_map.get(s['id'])
        if member is None or not member['retained']:
            continue
        job = main[s['id']];p = json.loads(job['payload']) if job['payload'] else {}
        sample_rows.append({'sample_id': s['id'], 'source': s['source'], 'path': member['relative_path'],
                            'sha256': s['sha'], 'size_bytes': s['size'], 'reference_functions': json.loads(s['refs']),
                            'reference_kind': 'directory_derived_not_human_verified', 'execution_state': job['state'],
                            'review_status': p.get('status'), 'function_code': p.get('prediction', {}).get('function_code'),
                            'confidence': p.get('prediction', {}).get('confidence'), 'error': job['error']})
    def jsonl(name, records):
        with (out / 'records' / name).open('w', encoding='utf-8') as f:
            for row in records:
                f.write(clean_text(json.dumps(portable(row), ensure_ascii=False), known) + '\n')
    jsonl('samples.jsonl', sample_rows)
    jsonl('release_membership.jsonl', members)
    jsonl('jobs.jsonl', [dict(r, retained=bool(member_map.get(r['sample_id'], {}).get('retained', 1))) for r in jobs])
    jsonl('requests.jsonl', [dict(r) for r in source.execute('SELECT * FROM requests ORDER BY id')])
    # Sanitize all textual DB values. JSON columns are parsed before path rewriting.
    tables = [r[0] for r in published.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for name in tables:
        columns = [r[1] for r in published.execute(f'PRAGMA table_info("{name}")') if r[2].upper() == 'TEXT']
        for row in published.execute(f'SELECT rowid,* FROM "{name}"').fetchall():
            names = [x[0] for x in published.execute(f'SELECT rowid,* FROM "{name}" LIMIT 0').description]
            mapping = dict(zip(names, row));changes = {}
            for column in columns:
                value = mapping.get(column)
                if not isinstance(value, str):
                    continue
                try:
                    decoded = json.loads(value)
                    value = json.dumps(portable(decoded), ensure_ascii=False)
                except (ValueError, TypeError):
                    value = portable(value)
                changes[column] = clean_text(value, known)
            if changes:
                sets = ','.join('"' + k + '"=?' for k in changes)
                published.execute(f'UPDATE "{name}" SET {sets} WHERE rowid=?', (*changes.values(), row[0]))
    for m in members:
        published.execute('UPDATE samples SET path=? WHERE id=?', (m['relative_path'], m['sample_id']))
    published.commit();published.execute('VACUUM');published.close();source.close()
    for name in ['FROZEN_RELEASE.json', 'config.json', 'summary.json', 'supplementary_analysis.json', 'manifest.json',
                 'plan_counts.json', 'classification.csv', 'review_queue.csv', 'gallery.html', 'controlled_gallery.html',
                 'report.md', 'supplementary_analysis.md']:
        path = root / name
        if path.exists():
            text = path.read_text(encoding='utf-8-sig')
            if path.suffix == '.json':
                write_json(out / 'benchmark' / name, json.loads(text), known)
            else:
                (out / 'benchmark' / name).write_text(clean_text(text, known).replace('../../docs/', '../reports/'), encoding='utf-8')
    for path in (WORKSPACE / 'docs').rglob('*'):
        if path.is_file():
            target = out / 'reports' / path.relative_to(WORKSPACE / 'docs');target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    if (root / 'figures').exists():
        shutil.copytree(root / 'figures', out / 'benchmark' / 'figures')
    mesh_entries = [(WORKSPACE / r['relative_path'], r['relative_path']) for r in members if r['retained']]
    archive_files(mesh_entries, out / 'meshes', 'meshes', known)
    render_entries = [(p, 'benchmark/assets/' + p.relative_to(root / 'assets').as_posix()) for p in sorted((root / 'assets').rglob('*')) if p.is_file()]
    archive_files(render_entries, out / 'renders', 'renders', known)
    # Audit/provenance archives are named without numeric sharding when each fits.
    audit_paths = archive_files([(p, 'benchmark/requests/' + p.name) for p in sorted((root / 'requests').glob('*.json'))], out / 'audit', 'requests', known)
    if len(audit_paths) == 1:
        audit_paths[0].rename(out / 'audit' / 'requests.zip')
    provenance = []
    for folder in ['source_snapshot', 'source_revisions', 'jobs', 'synthetic_inputs', 'organized_index']:
        for path in sorted((root / folder).rglob('*')):
            if path.is_file() and path.suffix not in {'.sqlite', '.pyc'}:
                provenance.append((path, 'benchmark/' + path.relative_to(root).as_posix()))
    for path in sorted((WORKSPACE / 'outputs' / 'publication').glob('*')):
        if path.is_file() and path.suffix in {'.json', '.csv'}:
            provenance.append((path, 'provenance/' + path.name))
    provenance_paths = archive_files(provenance, out / 'audit', 'provenance', known)
    if len(provenance_paths) == 1:
        provenance_paths[0].rename(out / 'audit' / 'provenance.zip')
    card = (WORKSPACE / 'docs' / 'DATASET_CARD.md').read_text(encoding='utf-8')
    (out / 'README.md').write_text(card, encoding='utf-8')
    files = sorted(p for p in out.rglob('*') if p.is_file())
    with (out / 'SHA256SUMS').open('w', encoding='utf-8') as stream:
        for path in files:
            with path.open('rb') as file:
                digest = hashlib.file_digest(file, 'sha256').hexdigest()
            stream.write(digest + '  ' + path.relative_to(out).as_posix() + '\n')
    print(json.dumps({'files': len(files) + 1, 'bytes': sum(p.stat().st_size for p in out.rglob('*') if p.is_file()),
                      'retained_models': len(sample_rows), 'out': str(out)}, indent=2), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('outputs/full_benchmark_v1'))
    p.add_argument('--out', type=Path, default=Path('dist/huggingface'))
    a = p.parse_args();build(a.root, a.out)
