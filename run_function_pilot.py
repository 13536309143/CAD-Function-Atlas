"""Run a function-only CAD pilot and copy selected meshes into functional groups."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import html
import json
from pathlib import Path
import platform
import shutil
import sys
from zoneinfo import ZoneInfo

from cad_agents import create_vision_client as MiniMaxVision
from cad_geometry import prepare_sample, render_views, file_hash, VIEWS, EXTRA_VIEWS
from cad_functions import (FUNCTIONS, DIRECTORY_REFERENCES, DEFAULT_SOURCES, TAXONOMY_VERSION,
                           infer_function, acceptance_issues, evaluate_functions)
from run_pilot import capability_probe, select_samples, write_json

ROOT = Path(__file__).resolve().parent
STATUS_NAMES = {'accepted': '自动接受', 'review_required': '待复核', 'unknown': '待确认功能',
                'error': '处理失败', 'preprocessed': '仅预处理'}


def classify_function(client, mesh, geometry, image, out, threshold, reuse_audits=False):
    agents = []
    for role in ('classifier', 'reviewer'):
        print(f'  {role}: function inference', flush=True)
        agents.append(infer_function(client, image, role, geometry, out, VIEWS, reuse_audits=reuse_audits))
    issues = acceptance_issues(agents, geometry, threshold)
    if not issues:
        return {'status': 'accepted', 'prediction': agents[0], 'agents': agents,
                'review_issues': [], 'rounds': 1}
    print('  supplemental views and function adjudication', flush=True)
    extra = out / 'additional_views.jpg'
    if not reuse_audits or not extra.exists():
        extra = render_views(mesh, out, extra=True)
    adjudicated = infer_function(client, extra, 'adjudicator', geometry, out, EXTRA_VIEWS, agents, reuse_audits=reuse_audits)
    return {'status': 'unknown' if adjudicated['function_code'] == 'unknown' else 'review_required',
            'prediction': adjudicated, 'agents': agents + [adjudicated],
            'review_issues': issues, 'rounds': 2}


def copy_grouped_models(records, out):
    """New copies only, with source hashes verified; source data are never moved."""
    destinations = []
    for record in records:
        source = Path(record['path'])
        if file_hash(source) != record['sha256']:
            raise ValueError(f"Source changed since sampling: {record['sample_id']}")
        code = (record.get('prediction') or {}).get('function_code', 'unknown')
        # code only originates from validated taxonomy, never model-provided paths.
        if code not in FUNCTIONS:
            raise ValueError('Invalid grouping function code')
        bucket = STATUS_NAMES[record['status']]
        target = out / 'organized_models' / bucket / f"{code}_{FUNCTIONS[code]['name']}" / (record['sample_id'] + source.suffix.lower())
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(str(target))
        shutil.copy2(source, target)
        if file_hash(target) != record['sha256']:
            raise ValueError('Copied model hash mismatch')
        record['organized_path'] = target.relative_to(out).as_posix()
        destinations.append(target)
    return destinations


def save_function_report(out, records, metadata):
    metrics = evaluate_functions(records)
    write_json(out / 'results.json', records)
    write_json(out / 'metrics.json', metrics)
    write_json(out / 'run.json', metadata)
    # No source part labels in the user-facing classification list.
    fields = ['sample_id', 'primary_function', 'secondary_functions', 'status',
              'function_statement', 'confidence', 'needs_context', 'model_path', 'views']
    with (out / 'classification.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            prediction = record.get('prediction') or {}
            writer.writerow({
                'sample_id': record['sample_id'],
                'primary_function': prediction.get('function_name', '处理失败' if record['status'] == 'error' else ''),
                'secondary_functions': '；'.join(FUNCTIONS[c]['name'] for c in prediction.get('secondary_functions', [])),
                'status': STATUS_NAMES[record['status']],
                'function_statement': prediction.get('function_statement', record.get('error', '')),
                'confidence': prediction.get('confidence', ''), 'needs_context': prediction.get('needs_context', ''),
                'model_path': record.get('organized_path', ''), 'views': f"{record['sample_id']}/views.jpg"})
    write_json(out / 'review_queue.json', [
        {'sample_id': r['sample_id'], 'proposed_function': (r.get('prediction') or {}).get('function_code'),
         'status': r['status'], 'views': f"{r['sample_id']}/views.jpg", 'confirmed_function': None,
         'reviewer_note': '', 'review_issues': r.get('review_issues', [])} for r in records])
    lines = ['# CAD 功能分类小量实测', '',
             f"样本数：{len(records)}；真实 API 请求：{metadata['api_calls']}；功能体系：{TAXONOMY_VERSION}。", '',
             '本次只输出功能大类。原始模型未移动；整理副本位于 `organized_models`，并按自动接受/待复核/待确认分区。', '',
             '## 功能结果', '', '| 样本 | 主要功能 | 状态 | 功能描述 | 图像 |', '|---|---|---|---|---|']
    cards = []
    esc = html.escape
    for r in records:
        p = r.get('prediction') or {}
        name = p.get('function_name', '处理失败')
        statement = p.get('function_statement', r.get('error', '')).replace('|', '/')
        lines.append(f"| {r['sample_id']} | {name} | {STATUS_NAMES[r['status']]} | {statement} | [8视图]({r['sample_id']}/views.jpg) |")
        image = f"{r['sample_id']}/views.jpg"
        cards.append('<article><h2>' + esc(r['sample_id'] + ' · ' + name) + '</h2><p>' + esc(STATUS_NAMES[r['status']] + ' · ' + statement) + '</p><img loading="lazy" src="' + image + '"><pre>' + esc(json.dumps(p or r.get('error', ''), ensure_ascii=False, indent=2)) + '</pre></article>')
    lines += ['', '## 评估口径', '',
              '目录到功能的参考集合在调用前冻结，仅作弱参考。它们不发送给模型，也不能代替人工确认的实际用途。', '']
    for key, title in [('directory_reference_match_rate', '功能参考集合匹配率'),
                       ('single_agent_reference_match_rate', '首个agent参考匹配率'),
                       ('auto_accept_coverage', '自动接受覆盖率'),
                       ('accepted_reference_match_rate', '接受子集参考匹配率')]:
        value = metrics[key]
        lines.append(f"- {title}：{value:.1%}" if value is not None else f'- {title}：暂无可评估结果')
    lines += ['', f"状态分布：{dict(Counter(STATUS_NAMES[r['status']] for r in records))}", '',
              '**人工确认的准确率：未测。** 未知和失败保留在参考匹配率分母。参考允许多功能时只检查主要功能是否属于预先定义的集合。', '',
              '## 功能体系', '', '| 功能大类 | 定义 |', '|---|---|']
    lines += [f"| {v['name']} | {v['definition']} |" for k, v in FUNCTIONS.items() if k != 'unknown']
    lines += ['', '## 判断边界', '',
              '- 主要功能可能依赖装配对象、载荷和介质，单件外形不足时保留待确认。',
              '- 同一视觉服务的不同agent会有相关错误；一致判断和主观分数不等于真实用途已证实。',
              '- 功能大类与上次具体零件名称是不同任务，匹配率不可直接作精度提升对比。',
              '- 本次仅小样本试验，未重新分类全部原始模型。源路径和目录参考只保留在追溯文件中。',
              '- 待复核结果也复制到对应建议功能目录，但位于“待复核”分区，尚未确认分类。', '',
              '清单：[classification.csv](classification.csv)；人工复核：[review_queue.json](review_queue.json)；多视图：[gallery.html](gallery.html)。']
    if metadata.get('fatal_error'):
        lines += ['', '运行错误：' + metadata['fatal_error']]
    if metadata.get('copy_error'):
        lines += ['', '整理副本错误：' + metadata['copy_error']]
    (out / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    page = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>CAD功能分类</title><style>body{font:16px system-ui;max-width:1200px;margin:30px auto;background:#f1f5f9;color:#123}article{background:white;border-radius:12px;padding:24px;margin:24px 0}img{width:100%}pre{white-space:pre-wrap}</style><h1>CAD 模型功能分类试验</h1><p>仅显示功能大类；待复核和未知项需要装配信息或人工确认。</p>' + ''.join(cards) + '</html>'
    (out / 'gallery.html').write_text(page, encoding='utf-8')
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT / 'data')
    parser.add_argument('--sources', nargs='+', default=DEFAULT_SOURCES, help='Source folders for sampling only, never inference hints')
    parser.add_argument('--per-source', type=int, default=1)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-calls', type=int, default=31)
    parser.add_argument('--threshold', type=float, default=.8)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.per_source < 1 or args.max_calls < 1 or not 0 <= args.threshold <= 1:
        parser.error('Invalid sample count, call budget or threshold')
    manifest = select_samples(args.data, args.sources, args.per_source, args.seed)
    for sample in manifest:
        sample['reference_functions'] = DIRECTORY_REFERENCES.get(sample['ground_truth'], [])
        sample['reference_kind'] = 'directory_derived_not_human_verified'
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')
    out = args.out or ROOT / 'outputs' / f'function_pilot_{stamp}'
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / 'manifest.json', manifest)
    write_json(out / 'function_taxonomy.json', {'version': TAXONOMY_VERSION, 'functions': FUNCTIONS})
    write_json(out / 'frozen_directory_references.json', DIRECTORY_REFERENCES)
    metadata = {'task': 'functional_classification_only', 'taxonomy_version': TAXONOMY_VERSION,
                'timestamp_asia_shanghai': stamp, 'python': platform.python_version(),
                'api_calls': 0, 'max_calls': args.max_calls, 'threshold': args.threshold,
                'mode': 'offline' if args.offline else 'minimax_token_plan',
                'source_policy': 'read-only originals; byte-identical copies for selected samples only',
                'input_policy': 'anonymous images, geometry and function definitions; no part labels or directory references',
                'provider': 'MiniMax Token Plan VLM; model ID not exposed'}
    records, client = [], None
    try:
        if not args.offline:
            client = MiniMaxVision(args.max_calls)
            from cad_providers import provider_identity
            metadata['provider'] = provider_identity()
            metadata['mode'] = metadata['provider']['provider']
            print('Checking real image understanding...', flush=True)
            capability_probe(client, out)
        for sample in manifest:
            record = dict(sample)
            print(sample['sample_id'] + ': functional classification', flush=True)
            folder = out / sample['sample_id']
            try:
                mesh, geometry, image = prepare_sample(Path(sample['path']), folder)
                record['geometry_warnings'] = geometry['warnings']
                if args.offline:
                    record.update(status='preprocessed', prediction=None)
                else:
                    record.update(classify_function(client, mesh, geometry, image, folder, args.threshold))
            except Exception as exc:
                message = client.redact(exc) if client else str(exc)
                record.update(status='error', prediction=None, error=message)
                print('  error: ' + message, flush=True)
            records.append(record)
            metadata['api_calls'] = client.calls if client else 0
            save_function_report(out, records, metadata)
    except Exception as exc:
        message = client.redact(exc) if client else str(exc)
        metadata['fatal_error'] = message
        print('Stopped: ' + message, flush=True)
        records.extend(dict(s, status='error', prediction=None, error=message) for s in manifest[len(records):])
    finally:
        metadata['api_calls'] = client.calls if client else 0
        if not args.offline:
            try:
                copied = copy_grouped_models(records, out)
                metadata['copied_models'] = len(copied)
            except Exception as exc:
                metadata['copy_error'] = client.redact(exc) if client else str(exc)
        metrics = save_function_report(out, records, metadata)
        print(json.dumps({'output': str(out.resolve()), 'metrics': metrics}, ensure_ascii=False, indent=2), flush=True)
    return int(bool(metadata.get('fatal_error') or metadata.get('copy_error') or any(r['status'] == 'error' for r in records)))


if __name__ == '__main__':
    sys.exit(main())
