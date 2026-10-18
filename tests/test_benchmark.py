from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from benchmark_store import initialize, connect, transaction
from benchmark_report import generate_report
from cad_benchmark import prepare_asset, APIController, Deferred
from cad_functions import build_prompt, validate_function
from cad_geometry import prepare_mesh, file_hash


@pytest.fixture
def planned(tmp_path):
    data = tmp_path / 'data'
    for source, extents in [('gear', [2, 3, 4]), ('spring', [1, 1, 1])]:
        folder = data / source
        folder.mkdir(parents=True)
        trimesh.creation.box(extents=extents).export(folder / 'model.obj')
    root = tmp_path / 'benchmark'
    initialize(root, data, cohort_per_source=1, max_calls=2)
    return root


def test_plan_covers_all_inputs_and_separates_control_experiments(planned):
    db = connect(planned)
    assert db.execute("SELECT count(*) FROM jobs WHERE experiment='full_multi'").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM jobs WHERE experiment='image_only'").fetchone()[0] == 2
    assert db.execute('SELECT count(*) FROM samples WHERE synthetic=1').fetchone()[0] == 10
    assert not (planned / 'source_snapshot' / '.env').exists()
    db.close()


def test_pending_jobs_are_not_reported_as_completed_predictions(planned):
    summary = generate_report(planned)
    main = summary['experiments']['full_multi']
    assert summary['complete'] is False
    assert main['scheduled'] == 2 and main['terminal'] == 0
    assert main['weak_reference_match_rate'] is None
    assert summary['human_verified_accuracy'] is None
    assert (planned / 'gallery.html').exists()


def test_augmentation_preserves_source_and_geometric_invariants(tmp_path):
    source = tmp_path / 'mesh.obj'
    trimesh.creation.box(extents=[2, 3, 4]).export(source)
    digest = file_hash(source)
    _, _, a = prepare_mesh(source)
    transform = trimesh.transformations.euler_matrix(.3, .4, .8)
    _, _, b = prepare_mesh(source, augmentation=transform)
    assert np.isclose(a['area_source_units_squared'], b['area_source_units_squared'])
    assert np.isclose(a['volume_source_units_cubed'], b['volume_source_units_cubed'])
    scale = np.eye(4)
    scale[:3, :3] *= 17
    scale[:3, 3] = [100, -30, 42]
    _, _, c = prepare_mesh(source, augmentation=scale)
    assert np.allclose(a['sorted_extent_ratios'], c['sorted_extent_ratios'])
    assert np.isclose(a['area_source_units_squared'] * 17**2, c['area_source_units_squared'])
    assert np.isclose(a['volume_source_units_cubed'] * 17**3, c['volume_source_units_cubed'])
    assert file_hash(source) == digest


def test_single_view_experiment_has_consistent_schema():
    prompt = build_prompt('classifier', {}, ['iso_a'], min_sources=1)
    assert '非unknown结果至少引用两个不同视角' not in prompt
    obj = {'function_code': 'transmission_drive', 'function_statement': '传递运动',
           'confidence': .8, 'needs_context': False, 'secondary_functions': [],
           'evidence': [{'view': 'iso_a', 'observation': '可见传动接口'}], 'uncertainties': []}
    validate_function(obj, {'iso_a'}, min_sources=1)
    with pytest.raises(ValueError):
        validate_function(obj, {'iso_a'})


def test_blank_ablation_contains_no_model_image(planned):
    from PIL import Image
    db = connect(planned)
    sample = dict(db.execute('SELECT * FROM samples WHERE synthetic=0 LIMIT 1').fetchone())
    db.close()
    payload = prepare_asset(planned, 'blank_test', sample, 'blank')
    pixels = np.asarray(Image.open(planned / payload['image']))
    assert pixels.min() == pixels.max() == 255
    assert set(payload['view_names']) == {'geometry_dimensions', 'geometry_topology'}


def test_parallel_requests_respect_persistent_budget(planned, monkeypatch):
    class FakeVision:
        key = 'fake-secret'
        host = 'https://api.minimaxi.com'
        def __init__(self, *args):
            pass
        def redact(self, value):
            return str(value).replace(self.key, '[REDACTED]')
        def call(self, image, prompt, path):
            content = '{"ok":true}'
            path.write_text(json.dumps({'response': {'content': content}, 'http_status': 200}))
            return content
    monkeypatch.setattr('cad_agents.MiniMaxVision', FakeVision)
    cfg = json.loads((planned / 'config.json').read_text(encoding='utf-8'))
    cfg['max_request_starts_per_second'] = 1000
    controller = APIController(planned, cfg)
    def send(i):
        try:
            return controller.request(f'job_{i}', 'classifier', Path('unused.jpg'), 'prompt')
        except Deferred:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(send, range(5)))
    db = connect(planned)
    assert db.execute('SELECT count(*) FROM requests').fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM requests WHERE state='complete'").fetchone()[0] == 2
    db.close()


def test_invalid_geometry_probes_fail_before_any_api_request(planned):
    db = connect(planned)
    samples = [dict(r) for r in db.execute("SELECT * FROM samples WHERE source='synthetic_invalid_geometry'")]
    for s in samples:
        with pytest.raises((ValueError, IndexError, TypeError)):
            prepare_mesh(Path(s['path']))
    assert db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
    db.close()


def test_asset_priority_query_uses_index_instead_of_per_asset_full_job_scan(planned):
    db = connect(planned)
    plan = ' '.join(str(tuple(r)) for r in db.execute("EXPLAIN QUERY PLAN SELECT a.id FROM assets a WHERE a.state='pending' ORDER BY (SELECT min(priority) FROM jobs j WHERE j.asset_id=a.id),a.id LIMIT 3"))
    assert 'SEARCH j USING COVERING INDEX' in plan
    db.close()


def test_human_evaluation_scores_only_explicit_terminal_annotations(planned, tmp_path):
    from evaluate_benchmark_human import evaluate_human
    import csv
    db = connect(planned)
    jobs = [dict(r) for r in db.execute("SELECT id,sample_id FROM jobs WHERE experiment='full_multi'")]
    db.execute("UPDATE jobs SET state='done',payload=? WHERE id=?", (json.dumps({'prediction': {'function_code': 'unknown'}, 'status': 'unknown'}), jobs[0]['id']))
    db.commit()
    db.close()
    labels = tmp_path / 'human.csv'
    with labels.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['sample_id', 'confirmed_function_code', 'reviewer_note'])
        writer.writerow([jobs[0]['sample_id'], 'unknown', 'Needs assembly information'])
        writer.writerow([jobs[1]['sample_id'], 'transmission_drive', 'Explicit annotation, inference pending'])
    report = evaluate_human(planned, labels)
    assert report['n_labels'] == 2 and report['n_evaluated_terminal'] == 1
    assert report['n_labelled_pending'] == 1
    assert report['human_accuracy_on_labelled_terminal_subset'] == 1
    assert report['unknown_recall_on_labelled_subset'] == 1
    assert report['accepted_human_accuracy_on_labelled_subset'] is None


def test_blank_human_review_rows_do_not_become_truth(planned, tmp_path):
    from evaluate_benchmark_human import evaluate_human
    db = connect(planned)
    sid = db.execute("SELECT sample_id FROM jobs WHERE experiment='full_multi' LIMIT 1").fetchone()[0]
    db.close()
    path = tmp_path / 'blank.csv'
    path.write_text('sample_id,confirmed_function_code\n' + sid + ',\n')
    with pytest.raises(ValueError, match='No explicit human labels'):
        evaluate_human(planned, path)


def test_postprocess_marks_partial_results_and_checks_original_hashes(planned):
    from benchmark_postprocess import analyze
    result = analyze(planned, verify_inputs=True)
    assert result['complete'] is False
    assert result['input_integrity']['unchanged'] == 2
    assert result['paired_exploratory_intervals'] == {}
    assert result['confidence_bins_weak_reference_only'] == []


def test_interrupted_request_with_complete_audit_reuses_response_without_network(planned, monkeypatch):
    class NoNetworkVision:
        def __init__(self, *args):
            pass
        def call(self, *args):
            raise AssertionError('Persisted completed response must not be charged again')
    monkeypatch.setattr('cad_agents.MiniMaxVision', NoNetworkVision)
    cfg = json.loads((planned / 'config.json').read_text(encoding='utf-8'))
    controller = APIController(planned, cfg)
    db = connect(planned)
    job = dict(db.execute("SELECT * FROM jobs WHERE experiment='full_multi' LIMIT 1").fetchone())
    prediction = {'function_code': 'transmission_drive', 'function_statement': '传递运动',
                  'confidence': .9, 'needs_context': False, 'secondary_functions': [],
                  'evidence': [{'view': 'top', 'observation': '周向结构'}, {'view': 'iso_a', 'observation': '中心接口'}], 'uncertainties': []}
    audit = {'http_status': 200, 'response': {'content': json.dumps(prediction), 'base_resp': {'status_code': 0}}}
    (planned / 'requests' / '000001.json').write_text(json.dumps(audit))
    db.execute('INSERT INTO requests(job_id,role,attempt,state,audit) VALUES(?,?,?,?,?)', (job['id'], 'classifier', 0, 'uncertain', 'requests/000001.json'))
    db.commit()
    db.close()
    result = controller.infer(job, 'classifier', {'geometry': {}, 'view_names': ['top', 'iso_a'], 'image': 'unused.jpg'})
    assert result['function_code'] == 'transmission_drive'
    db = connect(planned)
    assert db.execute('SELECT count(*) FROM requests').fetchone()[0] == 1
    assert db.execute('SELECT state FROM requests').fetchone()[0] == 'complete'
    db.close()


def test_token_plan_2056_defers_instead_of_failing_prediction(planned, monkeypatch):
    class QuotaVision:
        def __init__(self, *args):
            pass
        def redact(self, value):
            return str(value)
        def call(self, image, prompt, path):
            path.write_text(json.dumps({'http_status': 200, 'response': {'content': '',
                'base_resp': {'status_code': 2056, 'status_msg': '已达到 Token Plan 用量上限'}}}), encoding='utf-8')
            raise RuntimeError('MiniMax API status 2056: 已达到 Token Plan 用量上限')
    monkeypatch.setattr('cad_agents.MiniMaxVision', QuotaVision)
    cfg = json.loads((planned / 'config.json').read_text(encoding='utf-8'))
    controller = APIController(planned, cfg)
    with pytest.raises(Deferred, match='Provider quota/balance'):
        controller.request('test-job', 'classifier', Path('unused.jpg'), 'prompt')
    db = connect(planned)
    assert db.execute("SELECT count(*) FROM requests WHERE state='error'").fetchone()[0] == 1
    db.close()


def test_quota_refusals_do_not_exhaust_role_retries(planned, monkeypatch):
    class NoNetworkVision:
        def __init__(self, *args):
            pass
    monkeypatch.setattr('cad_agents.MiniMaxVision', NoNetworkVision)
    cfg = json.loads((planned / 'config.json').read_text(encoding='utf-8'))
    controller = APIController(planned, cfg)
    db = connect(planned)
    job = dict(db.execute("SELECT * FROM jobs WHERE experiment='full_multi' LIMIT 1").fetchone())
    for i in range(3):
        name = f'requests/quota_{i}.json'
        (planned / name).write_text(json.dumps({'http_status': 200, 'response': {
            'base_resp': {'status_code': 2056, 'status_msg': '用量上限'}}}), encoding='utf-8')
        db.execute('INSERT INTO requests(job_id,role,attempt,state,audit) VALUES(?,?,?,?,?)',
                   (job['id'], 'classifier', i, 'error', name))
    db.commit()
    db.close()
    prediction = {'function_code': 'transmission_drive', 'function_statement': '传递运动',
        'confidence': .9, 'needs_context': False, 'secondary_functions': [],
        'evidence': [{'view': 'top', 'observation': '周向结构'}, {'view': 'iso_a', 'observation': '中心接口'}], 'uncertainties': []}
    monkeypatch.setattr(controller, 'request', lambda *args: json.dumps(prediction))
    result = controller.infer(job, 'classifier', {'geometry': {}, 'view_names': ['top', 'iso_a'], 'image': 'unused.jpg'})
    assert result['function_code'] == 'transmission_drive'
    db = connect(planned)
    assert db.execute('SELECT count(*) FROM requests').fetchone()[0] == 3
    db.close()


@pytest.mark.parametrize('message', ['MiniMax returned no image understanding content', '[SSL: UNEXPECTED_EOF_WHILE_READING] interrupted'])
def test_transient_provider_failures_defer_and_preserve_attempt_budget(planned, monkeypatch, message):
    import time
    class TransientVision:
        def __init__(self, *args):
            pass
        def redact(self, value):
            return str(value)
        def call(self, image, prompt, path):
            path.write_text(json.dumps({'http_status': 200, 'response': {'content': '', 'base_resp': {'status_code': 0}}}))
            raise RuntimeError(message)
    monkeypatch.setattr('cad_agents.MiniMaxVision', TransientVision)
    cfg = json.loads((planned / 'config.json').read_text(encoding='utf-8'))
    controller = APIController(planned, cfg)
    with pytest.raises(Deferred, match='Transient network/provider response'):
        controller.request('temporary-job', 'classifier', Path('unused.jpg'), 'prompt')
    assert controller.blocked is None and controller.cooldown_until > time.monotonic()
    db = connect(planned)
    assert db.execute("SELECT count(*) FROM requests WHERE state='error'").fetchone()[0] == 1
    db.close()
