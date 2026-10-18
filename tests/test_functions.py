import json
from pathlib import Path

import pytest

from cad_functions import (validate_function, acceptance_issues, build_prompt,
                           evaluate_functions, infer_function, FUNCTIONS)
from cad_geometry import file_hash
from run_function_pilot import classify_function, copy_grouped_models


def prediction(code='transmission_drive', context=False):
    return {'function_code': code, 'function_statement': '传递旋转动力',
            'confidence': .9, 'needs_context': context, 'secondary_functions': [],
            'evidence': [{'view': 'top', 'observation': '周向重复啮合结构'},
                         {'view': 'iso_a', 'observation': '轴向贯通接口'}],
            'uncertainties': []}


def geometry():
    return {'invalid_vertices_removed': 0, 'invalid_or_degenerate_faces_removed': 0,
            'sorted_extent_ratios': [.2, 1, 1], 'scene_nodes': 1}


def test_function_codes_canonicalize_and_drop_specific_labels():
    obj = dict(prediction(), label_en='some specific part', function_name='invented')
    validated = validate_function(obj, {'top', 'iso_a'})
    assert validated['function_name'] == '动力与传动'
    assert 'label_en' not in validated
    with pytest.raises(ValueError):
        validate_function(dict(obj, function_code='gear'), {'top', 'iso_a'})


def test_function_evidence_requires_distinct_actual_views():
    obj = prediction()
    obj['evidence'][1]['view'] = 'top'
    with pytest.raises(ValueError, match='distinct'):
        validate_function(obj, {'top', 'iso_a'})
    obj['evidence'][1]['view'] = 'invented'
    with pytest.raises(ValueError, match='actual'):
        validate_function(obj, {'top', 'iso_a'})


def test_missing_context_prevents_auto_acceptance():
    assert not acceptance_issues([prediction(), prediction()], geometry(), .8)
    assert acceptance_issues([prediction(), prediction(context=True)], geometry(), .8)
    assert acceptance_issues([prediction(), prediction('support_guidance')], geometry(), .8)


def test_prompt_has_functions_but_no_source_labels_or_reference_mapping():
    prompt = build_prompt('classifier', geometry(), ['top', 'iso_a'])
    for source_label in ('bearing', 'washer', 'screws_and_bolts', 'ground_truth', 'reference_functions'):
        assert source_label not in prompt
    assert all(code in prompt for code in FUNCTIONS)


def test_ambiguous_reference_is_predefined_and_unknown_stays_in_denominator():
    records = [
        {'status': 'accepted', 'reference_functions': ['positioning_spacing', 'connection_fastening'],
         'prediction': prediction('positioning_spacing'), 'agents': [prediction('positioning_spacing')]},
        {'status': 'unknown', 'reference_functions': ['support_guidance'], 'prediction': prediction('unknown')},
        {'status': 'error', 'reference_functions': ['elastic_buffering'], 'prediction': None},
        {'status': 'review_required', 'reference_functions': [], 'prediction': prediction()},
    ]
    result = evaluate_functions(records)
    assert result['n_reference_evaluable'] == 3
    assert result['directory_reference_match_rate'] == 1 / 3
    assert result['auto_accept_coverage'] == .25
    assert result['human_verified_accuracy'] is None


def test_function_disagreement_gets_additional_views(monkeypatch, tmp_path):
    roles = []
    def fake_infer(client, image, role, geom, out, views, prior=None, **kwargs):
        roles.append(role)
        return prediction('support_guidance' if role == 'reviewer' else 'transmission_drive')
    monkeypatch.setattr('run_function_pilot.infer_function', fake_infer)
    monkeypatch.setattr('run_function_pilot.render_views', lambda *a, **kw: tmp_path / 'additional.jpg')
    result = classify_function(None, None, geometry(), tmp_path / 'views.jpg', tmp_path, .8)
    assert roles == ['classifier', 'reviewer', 'adjudicator']
    assert result['status'] == 'review_required'


def test_reorganization_copies_and_separates_pending_models(tmp_path):
    source = tmp_path / 'original.obj'
    source.write_text('v 0 0 0\n')
    digest = file_hash(source)
    original_bytes = source.read_bytes()
    records = [{'sample_id': 'sample_001', 'path': str(source), 'sha256': digest,
                'status': 'review_required', 'prediction': prediction()}]
    out = tmp_path / 'output'
    copied = copy_grouped_models(records, out)
    assert len(copied) == 1 and copied[0].read_bytes() == original_bytes
    assert source.read_bytes() == original_bytes
    assert '待复核' in records[0]['organized_path']
    with pytest.raises(FileExistsError):
        copy_grouped_models(records, out)


def test_changed_sources_are_not_copied(tmp_path):
    source = tmp_path / 'changed.obj'
    source.write_text('old')
    digest = file_hash(source)
    source.write_text('new')
    records = [{'sample_id': 'sample_001', 'path': str(source), 'sha256': digest,
                'status': 'accepted', 'prediction': prediction()}]
    with pytest.raises(ValueError, match='Source changed'):
        copy_grouped_models(records, tmp_path / 'output')


def test_malformed_response_gets_only_one_audited_repair(tmp_path):
    class FakeClient:
        def __init__(self):
            self.paths = []
        def call(self, image, prompt, audit_path):
            self.paths.append(audit_path.name)
            return '{"function_code":' if len(self.paths) == 1 else json.dumps(prediction())
    client = FakeClient()
    result = infer_function(client, Path('fake.jpg'), 'classifier', geometry(), tmp_path, ['top', 'iso_a'])
    assert result['function_code'] == 'transmission_drive'
    assert result['schema_repair_used'] is True
    assert client.paths == ['classifier_api.json', 'classifier_repair_api.json']


def test_invalid_repair_does_not_loop(tmp_path):
    class FakeClient:
        count = 0
        def call(self, *args):
            self.count += 1
            return '{"function_code":'
    client = FakeClient()
    with pytest.raises(ValueError):
        infer_function(client, Path('fake.jpg'), 'classifier', geometry(), tmp_path, ['top', 'iso_a'])
    assert client.count == 2


def test_recovery_reuses_good_independent_judgments_without_calls(tmp_path):
    (tmp_path / 'classifier.json').write_text(json.dumps(prediction()))
    class NoCalls:
        def call(self, *args):
            raise AssertionError('Valid completed role must not trigger a paid request')
    result = infer_function(NoCalls(), Path('fake.jpg'), 'classifier', geometry(), tmp_path, ['top', 'iso_a'], reuse_audits=True)
    assert result['function_code'] == 'transmission_drive'
