from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
import trimesh

from cad_agents import parse_json, validate_prediction, agent_prompt, MiniMaxVision, APIError
from cad_eval import evaluate, normalize_label
from cad_geometry import prepare_mesh
from run_pilot import acceptance, classify


def prediction(label='gear', confidence=.9):
    return {'label_zh': '齿轮' if label == 'gear' else label, 'label_en': label,
            'family': '传动件', 'confidence': confidence, 'unknown': False,
            'evidence': [{'view': 'top', 'observation': 'Repeated external teeth'},
                         {'view': 'iso_a', 'observation': 'Central through bore'}],
            'alternatives': [], 'uncertainties': []}


def geometry():
    return {'invalid_vertices_removed': 0, 'invalid_or_degenerate_faces_removed': 0,
            'sorted_extent_ratios': [.2, 1, 1], 'scene_nodes': 1}


def test_strict_normalization_preserves_open_vocabulary():
    assert normalize_label('spur gear') == 'gear'
    assert normalize_label('六角螺母') == 'nut'
    assert normalize_label('bearing housing') is None
    assert normalize_label('gearbox housing') is None
    assert normalize_label('unknown') is None


def test_abstentions_and_errors_stay_in_denominator():
    records = [
        {'sample_id': '1', 'ground_truth': 'gear', 'status': 'accepted', 'prediction': prediction()},
        {'sample_id': '2', 'ground_truth': 'gear', 'status': 'unknown', 'prediction': dict(prediction(), unknown=True)},
        {'sample_id': '3', 'ground_truth': 'washer', 'status': 'error', 'prediction': None},
        {'sample_id': '4', 'ground_truth': 'nut', 'status': 'review_required', 'prediction': prediction('nut')},
    ]
    metrics = evaluate(records)
    assert metrics['top1_accuracy_all_samples'] == .5
    assert metrics['auto_accept_coverage'] == .25
    assert metrics['accepted_accuracy'] == 1
    assert len(metrics['errors']) == 2


def test_parse_and_evidence_guard():
    assert parse_json('<think>private</think>```json\n{"a":1}\n```') == {'a': 1}
    good = prediction()
    validate_prediction(good, {'top', 'iso_a'})
    bad = deepcopy(good)
    bad['evidence'][0]['view'] = 'invented_camera'
    with pytest.raises(ValueError):
        validate_prediction(bad, {'top', 'iso_a'})
    bad = dict(good, confidence=float('nan'))
    with pytest.raises(ValueError):
        validate_prediction(bad, {'top', 'iso_a'})


@pytest.mark.parametrize('suffix', ['.obj', '.stl', '.glb'])
def test_format_and_solid_features(tmp_path, suffix):
    path = tmp_path / ('mesh' + suffix)
    trimesh.creation.box(extents=[2, 3, 4]).export(path)
    mesh, parts, features = prepare_mesh(path)
    assert np.allclose(mesh.bounds.mean(axis=0), 0)
    assert np.isclose(max(mesh.extents), 1)
    assert np.isclose(features['volume_source_units_cubed'], 24)
    assert features['coordinate_units'].startswith('unknown')


def test_open_mesh_volume_is_not_reported_as_solid(tmp_path):
    mesh = trimesh.creation.box()
    mesh.update_faces(np.arange(len(mesh.faces)) != 0)
    path = tmp_path / 'open.obj'
    mesh.export(path)
    _, _, features = prepare_mesh(path)
    assert not features['watertight']
    assert features['volume_source_units_cubed'] is None


def test_duplicate_faces_do_not_corrupt_topology(tmp_path):
    box = trimesh.creation.box()
    box.faces = np.vstack([box.faces, box.faces])
    path = tmp_path / 'duplicated.obj'
    box.export(path)
    _, _, features = prepare_mesh(path)
    assert features['duplicate_faces_removed'] == 12
    assert features['watertight']
    assert features['connected_components'] == 1
    assert np.isclose(features['volume_source_units_cubed'], 1)


def test_human_review_reports_subset_and_preserves_predictions(tmp_path):
    import json
    from review_results import review
    records = [
        {'sample_id': '1', 'ground_truth': 'bearing', 'status': 'accepted', 'prediction': prediction('washer')},
        {'sample_id': '2', 'ground_truth': 'gear', 'status': 'unknown', 'prediction': None},
    ]
    original = json.dumps(records)
    (tmp_path / 'results.json').write_text(original)
    labels = tmp_path / 'labels.json'
    labels.write_text(json.dumps([{'sample_id': '1', 'confirmed_label': 'washer'}]))
    result = review(tmp_path, labels)
    assert result['human_review_coverage'] == .5
    assert result['metrics_on_human_reviewed_subset']['top1_accuracy_all_samples'] == 1
    assert (tmp_path / 'results.json').read_text() == original


def test_glb_node_transforms_preserved(tmp_path):
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(), node_name='a')
    transform = np.eye(4)
    transform[0, 3] = 4
    scene.add_geometry(trimesh.creation.box(), node_name='b', transform=transform)
    path = tmp_path / 'assembly.glb'
    scene.export(path)
    _, parts, features = prepare_mesh(path)
    assert len(parts) == 2
    assert features['scene_nodes'] == 2
    assert np.allclose(features['extents_source_units'], [5, 1, 1])


def test_acceptance_requires_agreement_and_geometry_quality():
    assert not acceptance([prediction(), prediction()], geometry(), .8)
    assert acceptance([prediction(), prediction('pulley')], geometry(), .8)
    assert acceptance([prediction(), prediction(confidence=.4)], geometry(), .8)
    assert acceptance([prediction(), prediction()], dict(geometry(), scene_nodes=2), .8)


def test_prompt_contains_no_evaluation_label_or_candidate_list():
    prompt = agent_prompt('classifier', geometry())
    assert 'ground_truth' not in prompt
    assert 'screws_and_bolts' not in prompt
    assert 'data/' not in prompt


def test_disagreement_triggers_new_views_and_cannot_auto_accept(monkeypatch, tmp_path):
    roles = []
    def fake_infer(client, image, role, geom, out, views, prior=None):
        roles.append(role)
        if role == 'reviewer':
            return prediction('pulley')
        return prediction()
    monkeypatch.setattr('run_pilot.infer', fake_infer)
    monkeypatch.setattr('run_pilot.render_views', lambda *args, **kwargs: tmp_path / 'extra.jpg')
    result = classify(None, None, geometry(), tmp_path / 'views.jpg', tmp_path, .8)
    assert roles == ['classifier', 'reviewer', 'adjudicator']
    assert result['status'] == 'review_required'
    assert result['rounds'] == 2


def test_call_budget_stops_before_network(monkeypatch, tmp_path):
    monkeypatch.setenv('MINIMAX_API_KEY', 'test-key')
    monkeypatch.setenv('MINIMAX_API_HOST', 'https://api.minimaxi.com')
    client = MiniMaxVision(max_calls=0)
    with pytest.raises(APIError, match='budget exhausted'):
        client.call(tmp_path / 'nonexistent.jpg', 'prompt', tmp_path / 'audit.json')
