"""Run: python run_pilot.py [--offline] [--per-class 1] [--max-calls 20]."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import platform
import secrets
import sys
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from cad_agents import create_vision_client as MiniMaxVision, APIError, infer, parse_json
from cad_eval import evaluate, identity, normalize_label
from cad_geometry import prepare_sample, render_views, file_hash, VIEWS, EXTRA_VIEWS

ROOT = Path(__file__).resolve().parent
DEFAULT_CLASSES = ['bearing', 'gear', 'flange', 'screws_and_bolts', 'nut', 'washer']


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def select_samples(data, classes, per_class, seed):
    manifest = []
    for label in classes:
        folder = data / label
        if not folder.is_dir():
            raise ValueError(f'Category not found: {label}')
        paths = [p for p in folder.iterdir() if p.suffix.lower() in {'.obj', '.stl', '.glb'}]
        paths.sort(key=lambda p: hashlib.sha256((str(seed) + ':' + p.relative_to(data).as_posix()).encode()).hexdigest())
        # Include the user's active file in the default pilot, deterministically.
        active = folder / '0a0aae0bf6a01e737a1248629a66d3982fde54521e5fef84f9106b2310794c6c.obj'
        if label == 'bearing' and active in paths:
            paths.remove(active)
            paths.insert(0, active)
        if len(paths) < per_class:
            raise ValueError(f'{label} has only {len(paths)} supported files')
        for path in paths[:per_class]:
            digest = file_hash(path)
            manifest.append({'sample_id': f'sample_{len(manifest)+1:03d}',
                             'path': str(path.resolve()), 'ground_truth': label,
                             'sha256': digest, 'selection_seed': seed})
    return manifest


def capability_probe(client, out):
    """Random image-only code verifies actual image access, not text connectivity."""
    code = ''.join(secrets.choice('23456789ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(6))
    picture = Image.new('RGB', (900, 320), 'white')
    draw = ImageDraw.Draw(picture)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 96) if Path('C:/Windows/Fonts/arial.ttf').exists() else ImageFont.load_default(size=96)
    draw.text((70, 65), code, fill='black', font=font)
    draw.rectangle((650, 190, 820, 280), fill='red')
    target = out / 'vision_probe.png'
    picture.save(target)
    raw = client.call(target, '读取图片中的6位大写字母/数字代码，并判断右下角矩形颜色。仅输出JSON：{"code":"读出的代码", "rectangle_color":"英文颜色名"}。', out / 'probe_api.json')
    parsed = parse_json(raw)
    passed = parsed.get('code', '').strip().upper() == code and parsed.get('rectangle_color', '').strip().lower() == 'red'
    result = {'passed': passed, 'expected': {'code': code, 'rectangle_color': 'red'}, 'observed': parsed,
              'api_base': client.host, 'provider': getattr(client, 'provider', 'minimax')}
    write_json(out / 'capability.json', result)
    if not passed:
        raise APIError('Image capability probe failed; classification stopped')


def acceptance(predictions, geometry, threshold):
    a, b = predictions
    issues = []
    if a['unknown'] or b['unknown']:
        issues.append('One or both independent agents marked unknown')
    if identity(a) != identity(b):
        issues.append('Independent agents disagree on normalized class')
    if min(a['confidence'], b['confidence']) < threshold:
        issues.append('Subjective confidence below configured threshold')
    if geometry['invalid_vertices_removed'] or geometry['invalid_or_degenerate_faces_removed']:
        issues.append('Geometry cleaning removed invalid/degenerate elements')
    if min(geometry['sorted_extent_ratios']) <= 1e-7:
        issues.append('Near-planar or degenerate geometry')
    if geometry['scene_nodes'] > 1:
        issues.append('Multiple scene nodes require assembly-level review')
    # Open surfaces are permitted, but invalid volume is never sent as a valid measurement.
    return issues


def classify(client, mesh, features, image, out, threshold):
    predictions = []
    for role in ('classifier', 'reviewer'):
        print(f'  {role}: requesting MiniMax vision', flush=True)
        predictions.append(infer(client, image, role, features, out, VIEWS))
    issues = acceptance(predictions, features, threshold)
    if not issues:
        return {'status': 'accepted', 'prediction': predictions[0], 'agents': predictions,
                'decision_reason': 'Two independent prompts agree, meet score/evidence checks and pass geometry gates.',
                'rounds': 1, 'review_issues': []}
    print('  disagreement/uncertainty: rendering 4 additional views', flush=True)
    extra = render_views(mesh, out, extra=True)
    final = infer(client, extra, 'adjudicator', features, out, EXTRA_VIEWS, prior=predictions)
    # A single tiebreaker cannot auto-validate disputed classification.
    return {'status': 'unknown' if final['unknown'] else 'review_required',
            'prediction': final, 'agents': predictions + [final], 'rounds': 2,
            'review_issues': issues,
            'decision_reason': 'Additional-view adjudication completed; disputed/low-quality cases need human confirmation.'}


def save_report(out, records, metadata):
    summary = evaluate(records)
    if metadata['mode'] == 'offline':
        for key in ('top1_accuracy_all_samples', 'auto_accept_coverage', 'accepted_accuracy', 'macro_recall', 'n_correct_top1'):
            summary[key] = None
        summary['confusion'] = []
        summary['errors'] = [{'sample_id': r['sample_id'], 'error': r['error']} for r in records if r.get('error')]
        summary['per_class'] = {}
    else:
        baseline = [dict(r, prediction=(r.get('agents') or [None])[0],
                         status='review_required' if r.get('agents') else 'error') for r in records]
        summary['single_classifier_baseline'] = evaluate(baseline)
    write_json(out / 'results.json', records)
    write_json(out / 'metrics.json', summary)
    write_json(out / 'run.json', metadata)
    # Inspect all predictions: consensus errors also need human detection.
    write_json(out / 'review_queue.json', [
        {'sample_id': r['sample_id'], 'directory_label': r.get('ground_truth'),
         'proposed_label': (r.get('prediction') or {}).get('label_en'),
         'status': r['status'], 'review_issues': r.get('review_issues', []),
         'views': f"{r['sample_id']}/views.jpg", 'confirmed_label': None, 'reviewer_note': ''}
        for r in records])
    lines = ['# CAD 小规模验证报告', '', f"模式：{metadata['mode']}；API 请求次数：{metadata['api_calls']}。", '',
             '目录名称作为暂定真值；分类 agent 看不到目录名、文件名或类别列表。', '',
             f"样本数：{summary['n_total']}；状态：`{summary['status_counts']}`。", '']
    if metadata['mode'] == 'offline':
        lines += ['本次仅验证网格处理和渲染，没有分类准确率结果。', '']
    else:
        for key in ('top1_accuracy_all_samples', 'auto_accept_coverage', 'accepted_accuracy', 'macro_recall'):
            val = summary[key]
            lines.append(f"- {key}: {val:.1%}" if val is not None else f'- {key}: 无可评估样本')
        lines += ['', 'Top-1 按全部样本计算，未知和错误不从分母排除。待复核但给出类别的结果也列入 Top-1；自动接受准确率另报。', '']
        baseline_accuracy = summary['single_classifier_baseline']['top1_accuracy_all_samples']
        if baseline_accuracy is not None:
            lines += [f'同批样本首个识别 agent 的 Top-1 基线：{baseline_accuracy:.1%}。该对比仅用于发现流程影响，不能据此断言多 agent 普遍提升准确率。', '']
    lines += ['| 样本 | 目录真值 | 最终类别 | 状态 | 图像 |', '|---|---|---|---|---|']
    cards = []
    for r in records:
        sid = r['sample_id']
        pred = r.get('prediction') or {}
        label = pred.get('label_en', '')
        lines.append(f"| {sid} | {r.get('ground_truth') or ''} | {label.replace('|','/')} | {r['status']} | [8视图]({sid}/views.jpg) |")
        if r.get('error'):
            lines += ['', f"{sid} 错误：{r['error']}", '']
        esc = html.escape
        cards.append(f'<article><h2>{esc(sid)} · {esc(r.get("ground_truth") or "unlabelled")}</h2><p>{esc(label)} · {esc(r["status"])}</p><img src="{sid}/views.jpg"><pre>{esc(json.dumps(r.get("prediction") or r.get("error") or {}, ensure_ascii=False, indent=2))}</pre></article>')
    lines += ['', '## 限制与后续验证', '',
              '- 六类各一个样本只能验证流程是否贯通，不能估计总体准确率。',
              '- 多 agent 使用同一 MiniMax 视觉服务，错误可能相关；一致意见不等于独立真值。',
              '- confidence 是模型自报分数；阈值为试验设置，尚未校准。',
              '- 开放词汇输出未受32类限制，但本次仅评估目录内类别；未完成未知类/域外集的开放集评估。',
              '- 非封闭或法向不可靠网格不输出有效体积；单位未声明时不推断毫米。',
              '- 装配体按场景节点保留变换并导出；当前分类对象是整体，不自动为每个节点逐件分类。',
              '- 人工核对错误、分歧和同义词后，冻结规则并在新的分层留出集上复测。', '',
              '接口依据：[官方 Coding Plan MCP 源码](https://github.com/MiniMax-AI/MiniMax-Coding-Plan-MCP/blob/main/minimax_mcp/server.py)。']
    (out / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    page = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>CAD Pilot</title><style>body{font:16px system-ui;max-width:1200px;margin:32px auto;background:#f2f5f8;color:#123}article{background:white;padding:24px;margin-bottom:24px;border-radius:12px}img{width:100%}pre{white-space:pre-wrap;font-size:13px}</style><h1>CAD 多 Agent 小规模验证</h1><p>目录标签为暂定真值；分歧项需要人工复核。</p>' + ''.join(cards) + '</html>'
    (out / 'gallery.html').write_text(page, encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT / 'data')
    parser.add_argument('--classes', nargs='+', default=DEFAULT_CLASSES)
    parser.add_argument('--per-class', type=int, default=1)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--max-calls', type=int, default=20)
    parser.add_argument('--threshold', type=float, default=.8)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--mesh', type=Path, help='Single unlabelled OBJ/STL/GLB instead of directory evaluation')
    args = parser.parse_args()
    if args.per_class < 1 or args.max_calls < 1 or not 0 <= args.threshold <= 1:
        parser.error('per-class/max-calls must be positive and threshold within [0,1]')
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')
    out = args.out or ROOT / 'outputs' / f'pilot_{stamp}'
    out.mkdir(parents=True, exist_ok=False)
    manifest = ([{'sample_id': 'sample_001', 'path': str(args.mesh.resolve()), 'ground_truth': None,
                  'sha256': file_hash(args.mesh)}] if args.mesh else select_samples(args.data, args.classes, args.per_class, args.seed))
    write_json(out / 'manifest.json', manifest)
    metadata = {'mode': 'offline' if args.offline else 'minimax_token_plan', 'api_calls': 0,
                'threshold': args.threshold, 'max_calls': args.max_calls,
                'timestamp_asia_shanghai': stamp, 'seed': args.seed,
                'python': platform.python_version(),
                'provider': 'MiniMax Token Plan VLM; backend model ID not exposed by this endpoint',
                'classification_input': 'anonymized rendered views and geometry only; no labels or filename',
                'pipeline_version': 2}
    client = None
    records = []
    try:
        if not args.offline:
            client = MiniMaxVision(args.max_calls)
            from cad_providers import provider_identity
            metadata['provider'] = provider_identity()
            metadata['mode'] = metadata['provider']['provider']
            print('Checking real image understanding with random image-only code...', flush=True)
            capability_probe(client, out)
        for sample in manifest:
            print(f"{sample['sample_id']}: preparing mesh", flush=True)
            folder = out / sample['sample_id']
            record = dict(sample)
            try:
                mesh, features, image = prepare_sample(Path(sample['path']), folder)
                record['geometry_warnings'] = features['warnings']
                if args.offline:
                    record.update(status='preprocessed', prediction=None)
                else:
                    record.update(classify(client, mesh, features, image, folder, args.threshold))
            except Exception as exc:
                error = client.redact(exc) if client else str(exc)
                record.update(status='error', prediction=None, error=error)
                print(f"  error: {error}", flush=True)
            records.append(record)
            metadata['api_calls'] = client.calls if client else 0
            save_report(out, records, metadata)
    except Exception as exc:
        metadata['fatal_error'] = client.redact(exc) if client else str(exc)
        print('Stopped: ' + metadata['fatal_error'], flush=True)
        # Every selected sample stays in the evaluation denominator on fatal failure.
        for sample in manifest[len(records):]:
            records.append(dict(sample, status='error', prediction=None, error=metadata['fatal_error']))
    finally:
        metadata['api_calls'] = client.calls if client else 0
        summary = save_report(out, records, metadata)
        print(json.dumps({'output': str(out.resolve()), 'metrics': summary}, ensure_ascii=False, indent=2), flush=True)
    return 1 if metadata.get('fatal_error') or any(r['status'] == 'error' for r in records) else 0


if __name__ == '__main__':
    sys.exit(main())
