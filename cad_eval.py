"""Evaluation label normalization is downstream of open-vocabulary inference."""
from collections import Counter
import re

ALIASES = {
    'bearing': ['轴承', 'rolling bearing', 'ball bearing', 'roller bearing', '滚动轴承', '滚珠轴承', '滚子轴承'],
    'bushing': ['衬套', '轴套', 'plain bearing', 'sleeve bearing', 'bush'],
    'castors_and_wheels': ['脚轮', '车轮', 'castor', 'caster', 'wheel', 'caster wheel'],
    'chain_drive': ['链传动', '链轮', 'sprocket', 'chain sprocket', 'chain drive'],
    'clamp': ['夹具', '夹钳', '管夹'],
    'collar': ['轴环', 'shaft collar', 'collar ring'],
    'disc': ['盘', '圆盘', 'disk'],
    'fitting': ['管接头', '管件', 'pipe fitting', 'tube fitting'],
    'flange': ['法兰', 'flange plate'],
    'gear': ['齿轮', 'spur gear', 'helical gear', 'bevel gear', 'worm gear', '直齿轮', '斜齿轮', '锥齿轮'],
    'handles': ['手柄', '把手', 'handle', 'handwheel', '手轮'],
    'hinge': ['铰链', '合页'],
    'hook': ['挂钩', '吊钩', '钩'],
    'joint': ['接头', '联轴器', 'coupling', 'universal joint', '万向节'],
    'key_and_spline': ['键', '花键', 'key', 'spline', 'parallel key', 'machine key'],
    'linear_guide': ['直线导轨', 'linear guide', 'linear rail'],
    'motor': ['电机', 'electric motor'],
    'nut': ['螺母', 'hex nut', 'hexagonal nut', 'lock nut', 'wing nut', '六角螺母'],
    'pin': ['销', '销钉', 'dowel pin', 'clevis pin', '定位销'],
    'plate': ['板', '板件', 'mounting plate'],
    'plug': ['堵头', '塞', 'pipe plug'],
    'pulley': ['带轮', '皮带轮', 'sheave', 'timing pulley'],
    'ring': ['环', '环件', 'retaining ring', 'snap ring', '挡圈'],
    'rivet': ['铆钉', 'blind rivet'],
    'rotor': ['转子', 'impeller', '叶轮'],
    'screws_and_bolts': ['螺钉', '螺栓', 'screw', 'bolt', 'hex bolt', 'hex head bolt', 'socket head cap screw', 'cap screw', '螺丝'],
    'spacer': ['隔套', '间隔套', 'standoff', 'spacing sleeve'],
    'spring': ['弹簧', 'compression spring', 'tension spring', 'torsion spring'],
    'stud': ['螺柱', 'threaded stud', 'threaded rod', '双头螺柱'],
    'switch': ['开关', 'limit switch'],
    'valve': ['阀', '阀门', 'ball valve'],
    'washer': ['垫圈', '垫片', 'flat washer', 'plain washer', 'lock washer', '平垫圈'],
}


def clean(text):
    return re.sub(r'[\s_\-]+', ' ', str(text).lower()).strip()


LOOKUP = {clean(alias): key for key, aliases in ALIASES.items() for alias in [key, *aliases]}


def normalize_label(text):
    """Exact approved synonym mapping; unmatched open classes stay unmatched."""
    value = clean(text)
    if value in LOOKUP:
        return LOOKUP[value]
    # English regular plurals; no substring matching (bearing housing != bearing).
    if value.endswith('s') and value[:-1] in LOOKUP:
        return LOOKUP[value[:-1]]
    return None


def identity(prediction):
    return normalize_label(prediction.get('label_en', '')) or normalize_label(prediction.get('label_zh', '')) or clean(prediction.get('label_en', ''))


def evaluate(records):
    labelled = [r for r in records if r.get('ground_truth') is not None]
    accepted = [r for r in labelled if r.get('status') == 'accepted']
    totals, correct_by_class, confusion = Counter(), Counter(), Counter()
    correct = accepted_correct = 0
    errors = []
    for record in labelled:
        truth = normalize_label(record['ground_truth']) or record['ground_truth']
        prediction = record.get('prediction') or {}
        predicted = identity(prediction) if prediction else '<none>'
        totals[truth] += 1
        # Unknown is an abstention, even if a speculative label happens to match.
        ok = record.get('status') not in {'unknown', 'error', 'preprocessed'} and not prediction.get('unknown', False) and predicted == truth
        correct += int(ok)
        correct_by_class[truth] += int(ok)
        accepted_correct += int(ok and record.get('status') == 'accepted')
        confusion[(truth, predicted)] += 1
        if not ok:
            errors.append({'sample_id': record['sample_id'], 'ground_truth': truth,
                           'predicted': predicted, 'status': record['status']})
    n = len(labelled)
    return {
        'n_total': len(records), 'n_labelled': n,
        'n_accepted': len(accepted), 'n_correct_top1': correct,
        'top1_accuracy_all_samples': correct / n if n else None,
        'auto_accept_coverage': len(accepted) / n if n else None,
        'accepted_accuracy': accepted_correct / len(accepted) if accepted else None,
        'macro_recall': sum(correct_by_class[k] / v for k, v in totals.items()) / len(totals) if totals else None,
        'per_class': {k: {'n': v, 'correct': correct_by_class[k], 'recall': correct_by_class[k] / v} for k, v in totals.items()},
        'status_counts': dict(Counter(r['status'] for r in records)),
        'confusion': [{'truth': k[0], 'prediction': k[1], 'count': v} for k, v in sorted(confusion.items())],
        'errors': errors,
        'notes': ['Folder names are provisional ground truth, not human-verified engineering labels.',
                  'Unknown/error cases remain in the accuracy denominator.',
                  'Agent agreement and subjective confidence are not proof of correctness.',
                  'Unmapped synonyms are counted as unmatched; review them before changing the frozen alias table.'],
    }
