"""Adopt a live benchmark and recover coordinator crashes at most three times."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from benchmark_store import connect, transaction, atomic_json, now
from cad_benchmark import run_lock, APIController, query_quota


def coordinator_alive(root):
    try:
        with run_lock(root):
            return False
    except RuntimeError:
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    restarts = 0
    status_path = root / 'supervisor.json'
    if status_path.exists():
        restarts = json.loads(status_path.read_text(encoding='utf-8')).get('restarts', 0)
    with run_lock(root, 'supervisor.lock'):
        while True:
            if coordinator_alive(root):
                atomic_json(status_path, {'status': 'monitoring', 'at': now(), 'restarts': restarts})
                time.sleep(15)
                continue
            db = connect(root)
            pending = db.execute("SELECT count(*) FROM jobs WHERE state IN('pending','running')").fetchone()[0]
            state = json.loads(db.execute("SELECT value FROM state WHERE key='pipeline'").fetchone()[0])
            db.close()
            if not pending:
                from benchmark_postprocess import analyze
                analyze(root, verify_inputs=True)
                from benchmark_report import generate_report
                generate_report(root, final=True)
                atomic_json(status_path, {'status': 'complete', 'at': now(), 'restarts': restarts})
                return
            if state.get('status') == 'blocked':
                reason = state.get('reason') or ''
                if reason.startswith('Provider quota/balance'):
                    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
                    controller = APIController(root, config)
                    query_quota(root, controller)
                    if not controller.quota_wait:
                        atomic_json(status_path, {'status': 'blocked', 'at': now(), 'restarts': restarts, 'reason': reason})
                        return
                    # Latest coordinator knows how to wait for a documented low-quota window.
                else:
                    atomic_json(status_path, {'status': 'blocked', 'at': now(), 'restarts': restarts, 'reason': reason})
                    return
            if restarts >= 3:
                atomic_json(status_path, {'status': 'recovery_limit', 'at': now(), 'restarts': restarts, 'reason': state.get('reason')})
                return
            restarts += 1
            with transaction(root) as db:
                db.execute('INSERT INTO events(at,kind,payload) VALUES(?,?,?)', (now(), 'supervisor_restart', json.dumps({'number': restarts, 'previous_state': state})))
            stdout = (root / f'supervisor_run_{restarts}.stdout.log').open('a', encoding='utf-8')
            stderr = (root / f'supervisor_run_{restarts}.stderr.log').open('a', encoding='utf-8')
            with stdout, stderr:
                subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('cad_benchmark.py')), 'run', '--root', str(root)],
                                 cwd=str(Path(__file__).parent), stdout=stdout, stderr=stderr, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            atomic_json(status_path, {'status': 'restarting', 'at': now(), 'restarts': restarts})
            time.sleep(15)


if __name__ == '__main__':
    main()
