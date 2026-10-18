"""Function-only taxonomy, blinded prompts and reference evaluation."""
from __future__ import annotations

from collections import Counter
import json
import math

from cad_agents import parse_json

TAXONOMY_VERSION = 'function-v1'
FUNCTIONS = {
    'transmission_drive': {'name': '动力与传动', 'definition': '产生、传递或变换机械动力、扭矩、转速与运动。'},
    'support_guidance': {'name': '支承与导向', 'definition': '承受运动载荷并约束、引导相对旋转或直线运动，或降低运动摩擦。'},
    'connection_fastening': {'name': '连接与紧固', 'definition': '把两个或多个构件夹持、锁紧、连接或固定成整体。'},
    'positioning_spacing': {'name': '定位限位与间隔', 'definition': '确定相对位置、限制位移、维持间距或分散局部接触载荷。'},
    'sealing_closure': {'name': '密封与封堵', 'definition': '封闭开口、阻止泄漏或阻隔介质；不以主动调节介质为主要作用。'},
    'elastic_buffering': {'name': '弹性储能与缓冲', 'definition': '以弹性变形储存或释放能量、复位、吸振、缓冲或施加弹性预载。'},
    'fluid_control': {'name': '流体输送与控制', 'definition': '形成、连接或控制流体通道，输送、分配或调节介质。'},
    'operation_control': {'name': '操作与控制', 'definition': '接受人为操作、切换状态或提供控制/检测输入。'},
    'structural_support': {'name': '结构承载与防护', 'definition': '形成固定承载基础、安装骨架或防护外形；不以引导相对运动为主。'},
    'unknown': {'name': '待确认功能', 'definition': '现有网格不足以确定主要功能；需要装配关系或用途信息。'},
}

# Frozen before the pilot. Only used AFTER inference, never sent to agents.
# These are directory-derived hypotheses, not expert-verified functional truth.
DIRECTORY_REFERENCES = {
    'bearing': ['support_guidance'], 'bushing': ['support_guidance'],
    'castors_and_wheels': ['support_guidance'], 'linear_guide': ['support_guidance'],
    'hinge': ['support_guidance', 'connection_fastening'],
    'gear': ['transmission_drive'], 'pulley': ['transmission_drive'],
    'chain_drive': ['transmission_drive'], 'motor': ['transmission_drive'],
    'key_and_spline': ['transmission_drive'], 'rotor': ['transmission_drive', 'fluid_control'],
    'joint': ['transmission_drive', 'connection_fastening', 'fluid_control'],
    'screws_and_bolts': ['connection_fastening'], 'nut': ['connection_fastening'],
    'stud': ['connection_fastening'], 'rivet': ['connection_fastening'],
    'clamp': ['connection_fastening'], 'flange': ['connection_fastening', 'fluid_control'],
    'pin': ['positioning_spacing', 'connection_fastening'],
    'collar': ['positioning_spacing'], 'spacer': ['positioning_spacing'],
    'washer': ['connection_fastening', 'positioning_spacing'],
    'plug': ['sealing_closure'],
    'spring': ['elastic_buffering'],
    'fitting': ['fluid_control'], 'valve': ['fluid_control'],
    'handles': ['operation_control'], 'switch': ['operation_control'],
    'plate': ['structural_support', 'connection_fastening'],
    'hook': ['connection_fastening', 'structural_support'],
    # Generic ring/disc names imply no single function: intentionally unscored.
    'ring': [], 'disc': [],
}

DEFAULT_SOURCES = ['bearing', 'gear', 'screws_and_bolts', 'nut', 'washer',
                   'spring', 'valve', 'switch', 'plug', 'plate']


def build_prompt(role, geometry, view_names, prior=None, min_sources=2, geometry_only=False):
    roles = {
        'classifier': '你是机械功能分类工程师。独立判断该模型最可能承担的主要功能。',
        'reviewer': '你是独立功能复核工程师。未提供其他人的判断，请独立判断主要功能，特别检查形态相似但用途不同的情况。',
        'adjudicator': '你是功能争议裁决工程师。当前图片是同一模型的补充视角，结合新证据检验前两个功能假设；允许保留待确认。',
    }
    prompt = roles[role] + '''
任务仅按功能大类整理模型，不识别或输出具体零件名称、型号、标准号。
必须从下面功能代码中选一个主要功能。即使具体零件种类不能确定，只要主要功能有充分证据仍可选大类。
一个模型可能兼有多种作用，主要功能选一个，其余放secondary_functions（最多2个）。
仅凭圆环、圆孔、长杆、板状等通用形状不能证明装配用途；真正无法区分主要功能时用unknown。
CAD可能简化螺纹、滚动体及内部结构，缺少这些细节不能单独证明或排除某功能。
不要由Euler数、连通分量数量直接推断机械功能。单位未知，但无量纲比例仍可用。
needs_context仅在缺少装配/用途信息会改变主要功能时设true；不要因未知材料、尺寸或型号而机械地设true。
function_statement只描述作用，如“传递旋转动力”“限制轴向位移”，不写具体零件名称。
所有解释简洁，evidence仅2至3条，每条最多40个汉字，uncertainties最多3条。
仅输出JSON，无Markdown，格式为：
{"function_code":"功能代码", "function_statement":"一句功能描述", "confidence":0.0,
 "needs_context":false, "secondary_functions":[],
 "evidence":[{"view":"实际提供的视角名", "observation":"可见形状如何支持该功能"}],
 "uncertainties":[]}
confidence为主观分数，不是校准概率。非unknown结果至少引用两个不同视角。
功能定义：
''' + json.dumps(FUNCTIONS, ensure_ascii=False)
    prompt += '\n本次可引用视角仅限：' + ', '.join(view_names) + '。evidence.view逐字选用一个代码，不要改名或拼接。'
    prompt += '\n匿名几何测量：\n' + json.dumps(geometry, ensure_ascii=False)
    if min_sources != 2:
        prompt = prompt.replace('非unknown结果至少引用两个不同视角。', f'非unknown结果至少引用{min_sources}个实际提供的证据来源。')
    if geometry_only:
        prompt += '\n这是几何特征消融实验：图片只是空白占位，不包含模型，禁止据图片猜测形状。只能使用提供的数值测量；evidence.view使用geometry_dimensions或geometry_topology，分别引用尺寸比例或拓扑测量。无法确定功能时保留unknown。'
    if prior is not None:
        prompt += '\n此前独立结果（待检验假设，不是真值）：\n' + json.dumps(prior, ensure_ascii=False)
    return prompt


def validate_function(obj, view_names, min_sources=2):
    required = {'function_code', 'function_statement', 'confidence', 'needs_context',
                'secondary_functions', 'evidence', 'uncertainties'}
    if not isinstance(obj, dict) or required - obj.keys():
        raise ValueError('Missing function prediction fields')
    if obj['function_code'] not in FUNCTIONS:
        raise ValueError('Unknown function code')
    score = obj['confidence']
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('Invalid function confidence')
    if not isinstance(obj['needs_context'], bool):
        raise ValueError('needs_context must be boolean')
    if not isinstance(obj['function_statement'], str) or not obj['function_statement'].strip():
        raise ValueError('Missing function statement')
    secondaries = obj['secondary_functions']
    if not isinstance(secondaries, list) or len(secondaries) > 2 or any(not isinstance(c, str) or c not in FUNCTIONS or c in {obj['function_code'], 'unknown'} for c in secondaries) or len(set(secondaries)) != len(secondaries):
        raise ValueError('Invalid secondary functions')
    if not isinstance(obj['uncertainties'], list) or any(not isinstance(s, str) for s in obj['uncertainties']):
        raise ValueError('Invalid uncertainties')
    evidence = obj['evidence']
    if not isinstance(evidence, list):
        raise ValueError('evidence must be a list')
    for item in evidence:
        if not isinstance(item, dict) or item.get('view') not in view_names or not isinstance(item.get('observation'), str) or not item['observation'].strip():
            raise ValueError('Evidence must cite an actual supplied view')
    if obj['function_code'] != 'unknown' and len({item['view'] for item in evidence}) < min_sources:
        raise ValueError('Known function needs evidence from two distinct views')
    # Canonical names are generated locally, not copied from model text.
    return {k: obj[k] for k in required} | {'function_name': FUNCTIONS[obj['function_code']]['name']}


def infer_function(client, image, role, geometry, out, view_names, prior=None, reuse_audits=False):
    prompt = build_prompt(role, geometry, view_names, prior)
    parsed_path = out / f'{role}.json'
    api_path = out / f'{role}_api.json'
    if reuse_audits and parsed_path.exists():
        return validate_function(json.loads(parsed_path.read_text(encoding='utf-8')), set(view_names))
    if reuse_audits and api_path.exists():
        audit = json.loads(api_path.read_text(encoding='utf-8'))
        raw = audit.get('response', {}).get('content')
        if not isinstance(raw, str):
            raise ValueError('Original HTTP/API request failed; schema-only recovery cannot retry it')
        prompt = audit['prompt']
    else:
        raw = client.call(image, prompt, api_path)
    try:
        result = validate_function(parse_json(raw), set(view_names))
    except ValueError as exc:
        if (out / f'{role}_repair_api.json').exists():
            raise ValueError('A schema repair was already attempted; automatic repair limit reached') from None
        result = repair_function_output(client, image, role, prompt, raw, str(exc), out, view_names)
    (out / f'{role}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def repair_function_output(client, image, role, prompt, raw, error, out, view_names):
    """One explicit schema repair, sharing the SAME request budget; no HTTP retry."""
    repair_prompt = prompt + '\n上次输出未通过结构校验：' + error
    repair_prompt += '\n请重新检查图片，返回完整且格式正确的JSON；所有字段必须齐全，数组与对象必须闭合，不输出其他文字。上次输出仅供结构修复参考：\n' + raw
    repaired = client.call(image, repair_prompt, out / f'{role}_repair_api.json')
    result = validate_function(parse_json(repaired), set(view_names))
    result['schema_repair_used'] = True
    return result


def acceptance_issues(predictions, geometry, threshold):
    a, b = predictions
    issues = []
    if 'unknown' in {a['function_code'], b['function_code']}:
        issues.append('至少一个agent无法确定功能')
    if a['function_code'] != b['function_code']:
        issues.append('两个独立agent主要功能判断不一致')
    if min(a['confidence'], b['confidence']) < threshold:
        issues.append('模型主观分数未达到试验阈值')
    if a['needs_context'] or b['needs_context']:
        issues.append('需要装配关系或用途信息')
    if geometry['invalid_vertices_removed'] or geometry['invalid_or_degenerate_faces_removed']:
        issues.append('几何清理移除了无效元素')
    if min(geometry['sorted_extent_ratios']) <= 1e-7 or geometry['scene_nodes'] > 1:
        issues.append('退化形状或多节点整体需要复核')
    return issues


def evaluate_functions(records):
    evaluable = [r for r in records if r.get('reference_functions') and r['status'] != 'preprocessed']
    accepted = [r for r in records if r['status'] == 'accepted']
    def matched(r):
        return r['status'] not in {'error', 'unknown', 'preprocessed'} and (r.get('prediction') or {}).get('function_code') in r['reference_functions']
    matched_n = sum(matched(r) for r in evaluable)
    accepted_evaluable = [r for r in accepted if r.get('reference_functions')]
    baseline_correct = sum((r.get('agents') or [{}])[0].get('function_code') in r['reference_functions'] for r in evaluable)
    return {
        'n_total': len(records), 'n_reference_evaluable': len(evaluable),
        'status_counts': dict(Counter(r['status'] for r in records)),
        'function_counts': dict(Counter((r.get('prediction') or {}).get('function_code', 'error') for r in records)),
        'n_reference_matches': matched_n,
        'directory_reference_match_rate': matched_n / len(evaluable) if evaluable else None,
        'single_agent_reference_match_rate': baseline_correct / len(evaluable) if evaluable else None,
        'auto_accept_coverage': len(accepted) / len(records) if records else None,
        'accepted_reference_match_rate': sum(matched(r) for r in accepted_evaluable) / len(accepted_evaluable) if accepted_evaluable else None,
        'n_human_verified': 0, 'human_verified_accuracy': None,
        'notes': ['Reference sets are frozen directory-to-function assumptions, not human-confirmed truth.',
                  'Unknown and API/output errors stay in the evaluation denominator.',
                  'This coarse taxonomy pilot is not directly comparable to earlier part-name classification accuracy.'],
    }
