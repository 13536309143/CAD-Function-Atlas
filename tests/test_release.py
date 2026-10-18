import hashlib
import json
from pathlib import Path
import stat
import zipfile

import pytest
import trimesh

from benchmark_store import initialize, connect
from cad_benchmark import run
from download_dataset import extract_safely, verify
from freeze_release import cleanup_plan


def test_cleanup_plan_retains_failed_attempts_and_earlier_pilots(tmp_path):
    data = tmp_path / 'data' / 'gear';data.mkdir(parents=True)
    for index in range(3):
        trimesh.creation.box(extents=[1, 2, index + 1]).export(data / f'{index}.obj')
    root = tmp_path / 'outputs' / 'benchmark'
    initialize(root, data.parent, cohort_per_source=1)
    db = connect(root)
    samples = [dict(r) for r in db.execute('SELECT * FROM samples WHERE synthetic=0 ORDER BY path')]
    job = db.execute("SELECT id FROM jobs WHERE sample_id=? AND experiment='full_multi'", (samples[0]['id'],)).fetchone()[0]
    db.execute('INSERT INTO requests(job_id,role,attempt,state,error) VALUES(?,?,?,?,?)', (job, 'classifier', 0, 'error', 'network failure'))
    db.execute("UPDATE state SET value=? WHERE key='pipeline'", (json.dumps({'status': 'blocked', 'reason': 'Stopped by explicit user control command'}),))
    db.commit();db.close()
    pilot = tmp_path / 'outputs' / 'earlier_pilot';pilot.mkdir()
    (pilot / 'results.json').write_text(json.dumps([{'path': samples[1]['path'], 'error': 'API unavailable'}]))
    plan = cleanup_plan(tmp_path, root)
    assert plan['retained_models'] == 2 and plan['removed_models'] == 1
    manifest = json.loads((tmp_path / 'outputs/publication/cleanup_plan.json').read_text())
    assert [t['sample_id'] for t in manifest['targets']] == [samples[2]['id']]
    assert all(Path(s['path']).exists() for s in samples)  # Planning never deletes.


def test_frozen_release_refuses_inference_before_loading_credentials(tmp_path):
    (tmp_path / 'FROZEN_RELEASE.json').write_text('{}')
    with pytest.raises(ValueError, match='frozen'):
        run(tmp_path)


@pytest.mark.parametrize('name,is_link', [('../escape.txt', False), ('normal-link', True)])
def test_dataset_extraction_rejects_unsafe_archive_members(tmp_path, name, is_link):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        member = zipfile.ZipInfo(name)
        if is_link:
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(member, 'payload')
    with pytest.raises(ValueError):
        extract_safely(archive, tmp_path / 'extract')
    assert not (tmp_path / 'escape.txt').exists()


def test_dataset_integrity_detects_tampering(tmp_path):
    payload = tmp_path / 'metadata.json';payload.write_bytes(b'original')
    checksum = hashlib.sha256(b'original').hexdigest()
    (tmp_path / 'SHA256SUMS').write_text(checksum + '  metadata.json\n')
    verify(tmp_path)
    payload.write_bytes(b'changed')
    with pytest.raises(ValueError, match='Checksum mismatch'):
        verify(tmp_path)
