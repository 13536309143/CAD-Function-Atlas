"""Durable experiment plan, requests and results. No API credentials in the DB."""
from __future__ import annotations
from collections import defaultdict, Counter
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import platform
import importlib.metadata
from zoneinfo import ZoneInfo

from cad_functions import FUNCTIONS, DIRECTORY_REFERENCES, TAXONOMY_VERSION
from cad_geometry import file_hash

VERSION = 'benchmark-v1'
VARIANTS = {
    'full_multi': {'label': '全量：8视图+几何+多agent', 'asset': 'base', 'multi': True},
    'image_only': {'label': '消融：8视图无几何', 'asset': 'base', 'geometry': False},
    'geometry_only': {'label': '消融：仅几何，空白占位图', 'asset': 'blank', 'geometry_only': True},
    'single_iso': {'label': '消融：单个等轴测视图', 'asset': 'single_iso', 'min_sources': 1},
    'iso_pair': {'label': '消融：两个等轴测视图', 'asset': 'iso_pair'},
    'orthographic': {'label': '消融：六正交视图', 'asset': 'orthographic'},
    'rotate_a': {'label': '稳定性：三维旋转A', 'asset': 'rotate_a'},
    'rotate_b': {'label': '稳定性：三维旋转B', 'asset': 'rotate_b'},
    'scale_translate': {'label': '稳定性：17倍尺度及平移', 'asset': 'scale_translate'},
    'view_shuffle': {'label': '稳定性：视图拼接次序变化', 'asset': 'view_shuffle'},
    'low_resolution': {'label': '稳定性：视图分辨率减半', 'asset': 'low_resolution'},
    'appearance': {'label': '稳定性：背景和表面颜色变化', 'asset': 'appearance'},
    'repeat_1': {'label': '重复性：同输入独立调用1', 'asset': 'base'},
    'repeat_2': {'label': '重复性：同输入独立调用2', 'asset': 'base'},
    'format_stl': {'label': '格式一致性：STL重新导出', 'asset': 'format_stl'},
    'format_glb': {'label': '格式一致性：GLB重新导出', 'asset': 'format_glb'},
    'contextless_probe': {'label': '合成无上下文探针（非真实OOD基准）', 'asset': 'base', 'multi': True},
}


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')


def connect(root):
    db = sqlite3.connect(Path(root) / 'benchmark.sqlite', timeout=60)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=60000')
    return db


@contextmanager
def transaction(root):
    db = connect(root)
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def atomic_json(path, obj):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def initialize(root, data, cohort_per_source=2, seed=20261016, max_calls=65000):
    root, data = Path(root).resolve(), Path(data).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / 'assets').mkdir()
    (root / 'jobs').mkdir()
    (root / 'requests').mkdir()
    db = connect(root)
    db.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE samples(id TEXT PRIMARY KEY,path TEXT,source TEXT,sha TEXT,size INTEGER,refs TEXT,synthetic INTEGER,cohort INTEGER);
    CREATE TABLE assets(id TEXT PRIMARY KEY,sample_id TEXT,variant TEXT,state TEXT DEFAULT 'pending',payload TEXT,error TEXT,seconds REAL,started TEXT,finished TEXT);
    CREATE TABLE jobs(id TEXT PRIMARY KEY,sample_id TEXT,experiment TEXT,asset_id TEXT,priority INTEGER,state TEXT DEFAULT 'pending',payload TEXT,error TEXT,started TEXT,finished TEXT,seconds REAL);
    CREATE INDEX jobs_state ON jobs(state,priority);
    CREATE INDEX jobs_asset_priority ON jobs(asset_id,priority);
    CREATE INDEX assets_state ON assets(state);
    CREATE TABLE requests(id INTEGER PRIMARY KEY,job_id TEXT,role TEXT,attempt INTEGER,state TEXT,started TEXT,finished TEXT,audit TEXT,error TEXT,seconds REAL);
    CREATE UNIQUE INDEX request_unique ON requests(job_id,role,attempt);
    CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE events(id INTEGER PRIMARY KEY,at TEXT,kind TEXT,payload TEXT);
    ''')
    groups = defaultdict(list)
    files = sorted(p for p in data.rglob('*') if p.is_file() and p.suffix.lower() in {'.obj', '.stl', '.glb'})
    for path in files:
        rel = path.relative_to(data).as_posix()
        source = path.relative_to(data).parts[0]
        sid = 'm_' + hashlib.sha256(rel.encode()).hexdigest()[:16]
        item = {'id': sid, 'path': str(path), 'source': source, 'sha': file_hash(path),
                'size': path.stat().st_size, 'refs': DIRECTORY_REFERENCES.get(source, []), 'synthetic': 0}
        groups[source].append(item)
    for group in groups.values():
        group.sort(key=lambda s: hashlib.sha256((str(seed) + ':' + s['id']).encode()).hexdigest())
    seen_cohort = set()
    cohort, repeat_ids = set(), set()
    for source, group in sorted(groups.items()):
        selected = []
        for item in group:
            if item['sha'] not in seen_cohort:
                selected.append(item)
                seen_cohort.add(item['sha'])
            if len(selected) >= cohort_per_source:
                break
        cohort.update(s['id'] for s in selected)
        if selected:
            repeat_ids.add(selected[0]['id'])
    # Round-robin source strata so partial progress isn't just the biggest folder.
    ordered = []
    for i in range(max(map(len, groups.values()))):
        ordered.extend(group[i] for _, group in sorted(groups.items()) if i < len(group))
    synthetic = make_probes(root)
    all_samples = ordered + synthetic
    for item in all_samples:
        db.execute('INSERT INTO samples VALUES(?,?,?,?,?,?,?,?)',
                   (item['id'], item['path'], item['source'], item['sha'], item['size'],
                    json.dumps(item['refs']), item['synthetic'], int(item['id'] in cohort)))
    def asset(item, variant):
        # Mesh assets can safely share exact source bytes; source labels never enter cache keys.
        aid = hashlib.sha256((item['sha'] + ':' + variant + ':' + VERSION).encode()).hexdigest()[:24]
        db.execute('INSERT OR IGNORE INTO assets(id,sample_id,variant) VALUES(?,?,?)', (aid, item['id'], variant))
        return aid
    def job(item, experiment, priority):
        aid = asset(item, VARIANTS[experiment]['asset'])
        jid = item['id'] + '__' + experiment
        db.execute('INSERT INTO jobs(id,sample_id,experiment,asset_id,priority) VALUES(?,?,?,?,?)', (jid, item['id'], experiment, aid, priority))
    for index, item in enumerate(ordered):
        # All source files get fresh main inference; exact file duplicates share rendering only.
        job(item, 'full_multi', 0 if item['id'] in cohort else 1000 + index)
        if item['id'] in cohort:
            for exp in ('image_only', 'geometry_only', 'single_iso', 'iso_pair', 'orthographic',
                        'rotate_a', 'rotate_b', 'scale_translate', 'view_shuffle', 'low_resolution', 'appearance'):
                job(item, exp, 10)
        if item['id'] in repeat_ids:
            for exp in ('repeat_1', 'repeat_2', 'format_stl', 'format_glb'):
                job(item, exp, 20)
    for item in synthetic:
        job(item, 'contextless_probe', 5)
    from cad_providers import provider_identity
    config = {'version': VERSION, 'created': now(), 'data': str(data), 'seed': seed,
              'provider_identity': provider_identity(),
              'python': platform.python_version(), 'platform': platform.platform(),
              'dependencies': {p: importlib.metadata.version(p) for p in ('trimesh','numpy','pyvista','vtk','httpx','pillow')},
              'n_source_files': len(ordered), 'n_cohort': len(cohort), 'n_repeat_format_cohort': len(repeat_ids),
              'n_synthetic': len(synthetic), 'max_calls': max_calls, 'threshold': .8,
              'api_workers': 3, 'max_request_starts_per_second': 1,
              'taxonomy_version': TAXONOMY_VERSION, 'functions': FUNCTIONS,
              'references': DIRECTORY_REFERENCES, 'experiments': VARIANTS,
              'reference_warning': 'Directory-derived weak reference sets, not human functional truth.',
              'sampling_warning': 'Controlled experiments use a balanced cohort; only full_multi covers every source file.',
              'inference_cache_policy': 'Rendering shares exact source SHA; main inference is per file. Repeats always send fresh requests.'}
    db.execute('INSERT INTO state VALUES(?,?)', ('pipeline', json.dumps({'status': 'initialized', 'at': now()})))
    db.commit()
    manifest = [dict(r) for r in db.execute('SELECT * FROM samples')]
    atomic_json(root / 'config.json', config)
    atomic_json(root / 'manifest.json', manifest)
    atomic_json(root / 'plan_counts.json', {'jobs': dict(db.execute('SELECT experiment,count(*) FROM jobs GROUP BY experiment').fetchall()),
                                         'assets': db.execute('SELECT count(*) FROM assets').fetchone()[0],
                                         'unique_input_hashes': len({x['sha'] for x in ordered}),
                                         'source_counts': {k: len(v) for k, v in groups.items()}})
    snapshot = root / 'source_snapshot'
    snapshot.mkdir()
    for name in ('benchmark_store.py', 'cad_benchmark.py', 'benchmark_report.py', 'cad_geometry.py', 'cad_functions.py', 'cad_agents.py', 'cad_providers.py', 'requirements.lock.txt'):
        src = Path(__file__).with_name(name)
        if src.exists():
            (snapshot / name).write_bytes(src.read_bytes())
    db.close()
    return config


def make_probes(root):
    import numpy as np
    import trimesh
    folder = Path(root) / 'synthetic_inputs'
    folder.mkdir()
    rng = np.random.default_rng(20261016)
    shapes = {
        'sphere': trimesh.creation.icosphere(subdivisions=2),
        'cube': trimesh.creation.box(),
        'smooth_torus': trimesh.creation.torus(major_radius=1, minor_radius=.2),
        'flat_sheet': trimesh.creation.box(extents=(1, .7, .005)),
        'random_convex': trimesh.convex.convex_hull(rng.normal(size=(30, 3))),
        'open_shell': trimesh.creation.icosphere(subdivisions=1),
    }
    shapes['open_shell'].update_faces(np.arange(len(shapes['open_shell'].faces)) % 3 != 0)
    duplicated = trimesh.creation.box()
    duplicated.faces = np.vstack([duplicated.faces, duplicated.faces])
    shapes['duplicated_surface'] = duplicated
    result = []
    for index, (name, mesh) in enumerate(shapes.items()):
        path = folder / f'{name}.obj'
        mesh.export(path)
        result.append({'id': f'probe_{index+1:02d}', 'path': str(path.resolve()), 'source': 'synthetic_contextless',
                       'sha': file_hash(path), 'size': path.stat().st_size, 'refs': [], 'synthetic': 1})
    # Invalid files test preprocessor rejection, not model refusal.
    for index, content in enumerate(('', 'v nan 0 0\nv 0 1 0\nv 1 0 0\nf 1 2 3\n', 'v 0 0 0\nv 1 0 0\nv 2 0 0\nf 1 2 3\n')):
        path = folder / f'invalid_{index}.obj'
        path.write_text(content)
        result.append({'id': f'invalid_{index:02d}', 'path': str(path.resolve()), 'source': 'synthetic_invalid_geometry',
                       'sha': file_hash(path), 'size': path.stat().st_size, 'refs': [], 'synthetic': 1})
    return result
