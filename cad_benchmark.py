"""Resumable full-corpus CAD benchmark. init / prepare / run / report / status."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

from benchmark_store import connect, transaction, atomic_json, initialize, now, VARIANTS, VERSION


def prepare_asset(root, aid, sample, variant):
    """Runs in an isolated render process; never accesses credentials or APIs."""
    import numpy as np
    from PIL import Image
    import trimesh
    from cad_geometry import prepare_mesh, render_views, file_hash, VIEWS

    started = time.monotonic()
    root = Path(root)
    folder = root / 'assets' / aid
    folder.mkdir(parents=True, exist_ok=True)
    if file_hash(Path(sample['path'])) != sample['sha']:
        raise ValueError('Input SHA changed after benchmark initialization')
    transform = None
    if variant in {'rotate_a', 'rotate_b'}:
        angles = (37, 19, 53) if variant == 'rotate_a' else (91, 29, 7)
        transform = trimesh.transformations.euler_matrix(*np.radians(angles))
    elif variant == 'scale_translate':
        transform = np.eye(4)
        transform[:3, :3] *= 17
        transform[:3, 3] = [100, -30, 42]
    mesh, _, geometry = prepare_mesh(Path(sample['path']), augmentation=transform)
    conversion = None
    if variant.startswith('format_'):
        extension = variant.split('_', 1)[1]
        source_mesh = mesh.copy()
        source_mesh.vertices = source_mesh.vertices * max(geometry['extents_source_units']) + np.mean(geometry['bounds_source_units'], axis=0)
        converted = folder / ('converted.' + extension)
        source_mesh.export(converted)
        mesh, _, geometry = prepare_mesh(converted)
        conversion = 'Cleaned scene flattened to a single mesh; node/material annotations omitted. Numerical export precision may change.'
    definitions = dict(VIEWS)
    if variant == 'blank':
        image = folder / 'views.jpg'
        Image.new('RGB', (480, 480), 'white').save(image, quality=95)
        definitions = {'geometry_dimensions': None, 'geometry_topology': None}
    else:
        if variant == 'single_iso':
            definitions = {'iso_a': VIEWS['iso_a']}
        elif variant == 'iso_pair':
            definitions = {k: VIEWS[k] for k in ('iso_a', 'iso_b')}
        elif variant == 'orthographic':
            definitions = {k: v for k, v in VIEWS.items() if not k.startswith('iso')}
        elif variant == 'view_shuffle':
            definitions = {k: VIEWS[k] for k in ('iso_b', 'left', 'top', 'front', 'right', 'iso_a', 'back', 'bottom')}
        image = render_views(mesh, folder, tile=240 if variant == 'low_resolution' else 480,
                             definitions=definitions, background='#202631' if variant == 'appearance' else '#f2f5f8',
                             color='#d9b77a' if variant == 'appearance' else '#9cb8ca')
        if variant == 'base':
            render_views(mesh, folder, extra=True)
    payload = {'geometry': geometry, 'image': image.relative_to(root).as_posix(),
               'view_names': list(definitions), 'variant': variant, 'conversion_note': conversion,
               'seconds': time.monotonic() - started}
    atomic_json(folder / 'asset.json', payload)
    return payload


def prepare_loop(root, workers=3):
    # A separate process prevents VTK renderer crashes from killing API workers.
    with transaction(root) as db:
        db.execute("UPDATE assets SET state='pending' WHERE state='running'")
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', ('preparation', json.dumps({'status': 'running', 'pid': os.getpid(), 'at': now()})))
    completed = 0
    futures = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        while True:
            with transaction(root) as db:
                rows = db.execute("""SELECT a.*,s.path,s.sha FROM assets a JOIN samples s ON s.id=a.sample_id
                    WHERE a.state='pending' ORDER BY (SELECT min(priority) FROM jobs j WHERE j.asset_id=a.id),a.id LIMIT ?""",
                                  (max(0, workers - len(futures)),)).fetchall()
                for row in rows:
                    db.execute("UPDATE assets SET state='running',started=? WHERE id=?", (now(), row['id']))
                    f = pool.submit(prepare_asset, str(root), row['id'], dict(row), row['variant'])
                    futures[f] = row['id']
            if not futures:
                break
            ready, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
            for future in ready:
                aid = futures.pop(future)
                try:
                    payload = future.result()
                    with transaction(root) as db:
                        db.execute("UPDATE assets SET state='ready',payload=?,seconds=?,finished=? WHERE id=?",
                                   (json.dumps(payload, ensure_ascii=False), payload['seconds'], now(), aid))
                except Exception as exc:
                    with transaction(root) as db:
                        db.execute("UPDATE assets SET state='error',error=?,finished=? WHERE id=?", (str(exc)[:2000], now(), aid))
                completed += 1
                if completed % 25 == 0:
                    print(f'PREP completed={completed}', flush=True)
    with transaction(root) as db:
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', ('preparation', json.dumps({'status': 'complete', 'pid': os.getpid(), 'at': now()})))
    return completed


class Deferred(RuntimeError):
    pass


def is_provider_quota_error(audit, error=''):
    base = audit.get('response', {}).get('base_resp', {})
    message = (str(base.get('status_msg', '')) + ' ' + str(error)).lower()
    return str(base.get('status_code')) == '2056' or any(
        word in message for word in ('quota', 'balance', 'insufficient', '额度', '余额', '用量上限'))


class APIController:
    def __init__(self, root, config):
        from cad_agents import create_vision_client
        self.root, self.config = Path(root), config
        self.redactor = create_vision_client(config['max_calls'])
        self.lock = threading.Lock()
        self.next_start = 0.
        self.cooldown_until = 0.
        self.blocked = None
        self.quota_wait = False
        self.local = threading.local()

    def throttle(self):
        while True:
            with self.lock:
                if self.blocked:
                    raise Deferred(self.blocked)
                delay = max(self.next_start, self.cooldown_until) - time.monotonic()
                if delay <= 0:
                    self.next_start = time.monotonic() + 1 / self.config['max_request_starts_per_second']
                    return
            time.sleep(min(1, max(.01, delay)))

    def request(self, job_id, role, image, prompt):
        from cad_agents import create_vision_client
        self.throttle()
        with transaction(self.root) as db:
            count = db.execute('SELECT count(*) FROM requests').fetchone()[0]
            if count >= self.config['max_calls']:
                self.blocked = 'API call budget exhausted; remaining jobs are pending, not failed predictions.'
                raise Deferred(self.blocked)
            attempt = db.execute('SELECT count(*) FROM requests WHERE job_id=? AND role=?', (job_id, role)).fetchone()[0]
            audit = f'requests/{count+1:06d}.json'
            cursor = db.execute('INSERT INTO requests(job_id,role,attempt,state,started,audit) VALUES(?,?,?,?,?,?)',
                                (job_id, role, attempt, 'started', now(), audit))
            rid = cursor.lastrowid
        if not hasattr(self.local, 'client'):
            self.local.client = create_vision_client(self.config['max_calls'])
        t0 = time.monotonic()
        error = None
        content = None
        try:
            content = self.local.client.call(image, prompt, self.root / audit)
        except Exception as exc:
            error = self.redactor.redact(exc)
        elapsed = time.monotonic() - t0
        audit_path = self.root / audit
        body = json.loads(audit_path.read_text(encoding='utf-8')) if audit_path.exists() else {}
        body.update(global_request_id=rid, job_id=job_id, role=role, attempt=attempt)
        atomic_json(audit_path, body)
        with transaction(self.root) as db:
            db.execute('UPDATE requests SET state=?,finished=?,error=?,seconds=? WHERE id=?',
                       ('error' if error else 'complete', now(), error, elapsed, rid))
        if error:
            base = body.get('response', {}).get('base_resp', {})
            msg = str(base.get('status_msg', '')) + ' ' + error
            if body.get('http_status') in {401, 403} or base.get('status_code') == 1004:
                self.blocked = 'Authentication/permission error: ' + msg
                raise Deferred(self.blocked)
            if is_provider_quota_error(body, error):
                self.blocked = 'Provider quota/balance unavailable: ' + msg
                raise Deferred(self.blocked)
            if body.get('http_status') == 429 or any(word in msg.lower() for word in ('rate limit', 'frequency', '限流', '频率')):
                with self.lock:
                    self.cooldown_until = max(self.cooldown_until, time.monotonic() + 60)
                raise Deferred('Transient rate limit; cooldown 60 seconds')
            if body.get('http_status') in {408, 500, 502, 503, 504} or any(word in error.lower() for word in (
                    'no image understanding content', 'timed out', 'disconnected', 'getaddrinfo',
                    'unexpected_eof', 'winerror 10051', 'winerror 10054', 'connection reset')):
                with self.lock:
                    self.cooldown_until = max(self.cooldown_until, time.monotonic() + 10)
                raise Deferred('Transient network/provider response; cooldown 10 seconds; per-role attempt limit still applies')
            raise RuntimeError(error)
        return content

    def infer(self, job, role, asset, prior=None):
        from cad_functions import build_prompt, validate_function
        from cad_agents import parse_json
        cfg = VARIANTS[job['experiment']]
        geometry = asset['geometry'] if cfg.get('geometry', True) else {'note': 'Geometry intentionally omitted for image-only ablation.'}
        min_sources = cfg.get('min_sources', 2)
        prompt = build_prompt(role, geometry, asset['view_names'], prior,
                              min_sources=min_sources, geometry_only=cfg.get('geometry_only', False))
        path = self.root / 'jobs' / job['id'] / (role + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return validate_function(json.loads(path.read_text(encoding='utf-8')), set(asset['view_names']), min_sources)
        db = connect(self.root)
        attempts = [dict(r) for r in db.execute('SELECT * FROM requests WHERE job_id=? AND role=? ORDER BY id', (job['id'], role))]
        db.close()
        last_raw, validation_error = None, None
        quota_attempt_ids = set()
        for attempt in attempts:
            audit_path = self.root / attempt['audit']
            if not audit_path.exists():
                continue
            try:
                audit = json.loads(audit_path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                continue
            if is_provider_quota_error(audit, attempt.get('error', '')):
                quota_attempt_ids.add(attempt['id'])
                continue
            raw = audit.get('response', {}).get('content')
            if attempt['state'] == 'uncertain' and 200 <= audit.get('http_status', 0) < 300 and audit.get('response', {}).get('base_resp', {}).get('status_code', 0) == 0 and isinstance(raw, str):
                # HTTP audit proves a response arrived even if the process died before DB commit.
                with transaction(self.root) as db:
                    db.execute("UPDATE requests SET state='complete',finished=?,error=? WHERE id=?", (now(), 'Recovered completed response from persisted HTTP audit; no new API request.', attempt['id']))
                    db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)', (now(), 'audit_response_recovered', json.dumps({'request_id': attempt['id']})))
                attempt['state'] = 'complete'
            if attempt['state'] != 'complete' or not isinstance(raw, str):
                continue
            try:
                result = validate_function(parse_json(raw), set(asset['view_names']), min_sources)
                atomic_json(path, result)
                return result
            except ValueError as exc:
                last_raw, validation_error = raw, str(exc)
        # Quota refusals remain in the global budget/audit but cannot exhaust a role's
        # retry allowance before a refreshed window can produce a model response.
        attempts = [a for a in attempts if a['id'] not in quota_attempt_ids]
        # At most 3 non-quota attempts per role, including HTTP retries and one schema repair.
        malformed_seen = sum(1 for a in attempts if a['state'] == 'complete')
        if len(attempts) >= 3 or malformed_seen >= 2:
            raise ValueError('Per-role attempt/JSON repair limit reached')
        if last_raw is not None:
            prompt += '\n上次结构校验失败：' + validation_error + '\n请返回完整JSON。上次输出：\n' + last_raw
        raw = self.request(job['id'], role, self.root / asset['image'], prompt)
        try:
            result = validate_function(parse_json(raw), set(asset['view_names']), min_sources)
        except ValueError:
            # Re-entry reads the successful HTTP audit and performs one explicit repair.
            return self.infer(job, role, asset, prior)
        atomic_json(path, result)
        return result


def process_job(root, job, controller):
    from cad_functions import acceptance_issues
    from cad_geometry import EXTRA_VIEWS
    t0 = time.monotonic()
    db = connect(root)
    asset_row = db.execute('SELECT * FROM assets WHERE id=?', (job['asset_id'],)).fetchone()
    db.close()
    asset = json.loads(asset_row['payload'])
    agents = [controller.infer(job, 'classifier', asset)]
    issues = []
    if VARIANTS[job['experiment']].get('multi'):
        agents.append(controller.infer(job, 'reviewer', asset))
        issues = acceptance_issues(agents, asset['geometry'], controller.config['threshold'])
        if issues:
            extra = dict(asset, image=f"assets/{job['asset_id']}/additional_views.jpg", view_names=list(EXTRA_VIEWS))
            agents.append(controller.infer(job, 'adjudicator', extra, agents.copy()))
        status = 'accepted' if not issues else ('unknown' if agents[-1]['function_code'] == 'unknown' else 'review_required')
    else:
        status = 'unknown' if agents[0]['function_code'] == 'unknown' else 'predicted'
    result = {'prediction': agents[-1], 'agents': agents, 'status': status, 'review_issues': issues,
              'image': asset['image'], 'seconds': time.monotonic() - t0}
    atomic_json(Path(root) / 'jobs' / job['id'] / 'result.json', result)
    return result


def query_quota(root, controller):
    if getattr(controller.redactor, 'provider', 'minimax') != 'minimax':
        return  # Other vendors have no MiniMax Token Plan quota endpoint.
    import httpx
    host = 'https://www.minimaxi.com' if controller.redactor.host.endswith('minimaxi.com') else 'https://www.minimax.io'
    try:
        response = httpx.get(host + '/v1/token_plan/remains',
                             headers={'Authorization': 'Bearer ' + controller.redactor.key}, timeout=20)
        data = response.json()
        safe = json.loads(controller.redactor.redact(json.dumps(data)))
        with transaction(root) as db:
            db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)', (now(), 'quota', json.dumps(safe)))
        atomic_json(Path(root) / 'latest_quota.json', {'at': now(), 'response': safe})
        low_quota = False
        known_percentages = []
        for entry in safe.get('model_remains', []):
            for field in ('current_interval_remaining_percent', 'current_weekly_remaining_percent'):
                if isinstance(entry.get(field), (int, float)):
                    known_percentages.append(entry[field])
                    low_quota |= entry[field] <= 1
        if low_quota:
            controller.blocked = 'Provider remaining quota at or below 1%; waiting for quota window recovery.'
            controller.quota_wait = True
        elif known_percentages and controller.quota_wait and min(known_percentages) > 2:
            controller.quota_wait = False
            controller.blocked = None
    except Exception as exc:
        with transaction(root) as db:
            db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)', (now(), 'quota_query_error', controller.redactor.redact(exc)))


@contextmanager
def run_lock(root, filename='runner.lock'):
    path = Path(root) / filename
    with path.open('a+b') as stream:
        stream.seek(0)
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another coordinator is already using this benchmark') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def run(root, prepare_workers=3, api_workers=3):
    from benchmark_report import generate_report
    root = Path(root).resolve()
    if (root / 'FROZEN_RELEASE.json').exists():
        raise ValueError('This benchmark is a frozen, pruned release. Initialize a NEW root to run new experiments.')
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    controller = APIController(root, config)
    from cad_providers import provider_identity
    actual = provider_identity()
    frozen = config.get('provider_identity', {'provider': 'minimax', 'model': 'provider-managed', 'base_url': actual['base_url']})
    if actual != frozen:
        raise ValueError('Provider/model/base URL differs from this run configuration; initialize a NEW root for a fair comparison.')
    # A process pool belongs to a dedicated prep process; main process serves HTTP threads.
    import subprocess
    prep = None
    try:
        with run_lock(root, 'prepare.lock'):
            prep = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), 'prepare', '--root', str(root), '--workers', str(prepare_workers)],
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except RuntimeError:
        print('Existing geometry preparer will be reused.', flush=True)
    with transaction(root) as db:
        db.execute("UPDATE jobs SET state='pending' WHERE state='running'")
        db.execute("UPDATE requests SET state='uncertain' WHERE state='started'")
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', ('pipeline', json.dumps({'status': 'running', 'at': now(), 'pid': os.getpid()})))
    query_quota(root, controller)
    if not controller.blocked:
        try:
            capability = root / 'capability.json'
            if not capability.exists() or not json.loads(capability.read_text(encoding='utf-8')).get('passed'):
                from run_pilot import capability_probe
                class ProbeAdapter:
                    host = controller.redactor.host
                    provider = getattr(controller.redactor, 'provider', 'minimax')
                    def call(self, image, prompt, audit_path):
                        return controller.request('capability', 'probe', image, prompt)
                capability_probe(ProbeAdapter(), root)
        except Exception as exc:
            controller.blocked = 'Vision capability probe failed: ' + controller.redactor.redact(exc)
    futures, last_report, last_quota = {}, 0., time.monotonic()
    with ThreadPoolExecutor(max_workers=api_workers) as pool:
        while True:
            with transaction(root) as db:
                # Invalid mesh handling stays separate from image-API refusal.
                db.execute("""UPDATE jobs SET state='error',error=(SELECT error FROM assets WHERE assets.id=jobs.asset_id),finished=?
                    WHERE state='pending' AND asset_id IN(SELECT id FROM assets WHERE state='error')""", (now(),))
                rows = [] if controller.blocked else db.execute("""SELECT j.* FROM jobs j JOIN assets a ON j.asset_id=a.id
                    WHERE j.state='pending' AND a.state='ready' ORDER BY priority,j.id LIMIT ?""", (max(0, api_workers-len(futures)),)).fetchall()
                for row in rows:
                    db.execute("UPDATE jobs SET state='running',started=? WHERE id=?", (now(), row['id']))
                    future = pool.submit(process_job, root, dict(row), controller)
                    futures[future] = row['id']
                pending = db.execute("SELECT count(*) FROM jobs WHERE state IN('pending','running')").fetchone()[0]
            if futures:
                ready, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                for future in ready:
                    jid = futures.pop(future)
                    try:
                        result = future.result()
                        with transaction(root) as db:
                            db.execute("UPDATE jobs SET state='done',payload=?,seconds=?,finished=? WHERE id=?", (json.dumps(result, ensure_ascii=False), result['seconds'], now(), jid))
                    except Deferred as exc:
                        with transaction(root) as db:
                            db.execute("UPDATE jobs SET state='pending',error=? WHERE id=?", (str(exc), jid))
                    except Exception as exc:
                        with transaction(root) as db:
                            db.execute("UPDATE jobs SET state='error',error=?,finished=? WHERE id=?", (controller.redactor.redact(exc), now(), jid))
            else:
                time.sleep(1)
            if time.monotonic() - last_quota >= 180:
                query_quota(root, controller)
                last_quota = time.monotonic()
            if time.monotonic() - last_report >= 30:
                state = {'status': 'waiting_for_quota' if controller.quota_wait else 'blocked' if controller.blocked else 'running', 'reason': controller.blocked,
                         'at': now(), 'pid': os.getpid(), 'prep_pid': prep.pid if prep else None}
                with transaction(root) as db:
                    db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', ('pipeline', json.dumps(state)))
                summary = generate_report(root, final=False)
                print(json.dumps({'at': now(), 'progress': summary['progress'], 'api_calls': summary['api_calls'], 'state': state}, ensure_ascii=True), flush=True)
                last_report = time.monotonic()
            if not pending and not futures:
                break
            if controller.blocked and not controller.quota_wait and not futures:
                if controller.blocked.startswith('Provider quota/balance'):
                    query_quota(root, controller)
                    if controller.quota_wait:
                        continue
                # Quota/auth issues never cause thousands of fabricated error predictions.
                break
            if prep and prep.poll() not in (None, 0) and not futures:
                controller.blocked = 'Geometry worker process exited unexpectedly; rerun to resume.'
                break
    state = {'status': 'blocked' if controller.blocked else 'complete', 'reason': controller.blocked, 'at': now(),
             'pid': os.getpid(), 'prep_pid': prep.pid if prep else None, 'prep_continues': prep.poll() is None if prep else True}
    with transaction(root) as db:
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', ('pipeline', json.dumps(state)))
    generate_report(root, final=not controller.blocked)
    print(json.dumps(state, ensure_ascii=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'prepare', 'run', 'report', 'status'])
    parser.add_argument('--root', type=Path, default=Path('outputs/full_benchmark_v1'))
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--api-workers', type=int, default=3)
    parser.add_argument('--max-calls', type=int, default=65000)
    args = parser.parse_args()
    if args.command != 'init':
        with transaction(args.root) as db:
            db.execute('CREATE INDEX IF NOT EXISTS jobs_asset_priority ON jobs(asset_id,priority)')
    if args.command == 'init':
        config = initialize(args.root, args.data, max_calls=args.max_calls)
        print(json.dumps(config | {'functions': 'saved', 'references': 'saved', 'experiments': 'saved'}, ensure_ascii=True, indent=2))
    elif args.command == 'prepare':
        with run_lock(args.root, 'prepare.lock'):
            prepare_loop(args.root, args.workers)
    elif args.command == 'run':
        with run_lock(args.root):
            try:
                run(args.root, args.workers, args.api_workers)
            except BaseException as exc:
                with transaction(args.root) as db:
                    db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', ('pipeline', json.dumps({'status': 'crashed', 'reason': str(exc)[:1000], 'at': now(), 'pid': os.getpid()})))
                from benchmark_report import generate_report
                generate_report(args.root)
                raise
    elif args.command == 'status':
        db = connect(args.root)
        progress = {'assets': dict(db.execute('SELECT state,count(*) FROM assets GROUP BY state').fetchall()),
                    'jobs': dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state').fetchall()),
                    'requests': dict(db.execute('SELECT state,count(*) FROM requests GROUP BY state').fetchall()),
                    'full_test': dict(db.execute("SELECT state,count(*) FROM jobs WHERE experiment='full_multi' GROUP BY state").fetchall())}
        state = db.execute("SELECT value FROM state WHERE key='pipeline'").fetchone()
        db.close()
        print(json.dumps({'progress': progress, 'runner_state': json.loads(state[0]) if state else None}, ensure_ascii=True, indent=2))
    else:
        from benchmark_report import generate_report
        summary = generate_report(args.root)
        print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
