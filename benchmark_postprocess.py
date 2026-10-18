"""Supplementary diagnostics and final input integrity check; never calls the API."""
from collections import defaultdict, Counter
import argparse
import json
from pathlib import Path

import numpy as np

from benchmark_store import connect, atomic_json, now
from cad_geometry import file_hash


def analyze(root, verify_inputs=True):
    root = Path(root)
    db = connect(root)
    rows = [dict(r) for r in db.execute('''SELECT j.*,s.source,s.refs,s.path,s.sha,s.synthetic,s.cohort
        FROM jobs j JOIN samples s ON s.id=j.sample_id''')]
    assets = {r['id']: json.loads(r['payload']) for r in db.execute("SELECT id,payload FROM assets WHERE state='ready'")}
    inputs = [dict(r) for r in db.execute('SELECT * FROM samples WHERE synthetic=0')]
    requests = [dict(r) for r in db.execute('SELECT * FROM requests')]
    db.close()
    for r in rows:
        r['refs'] = json.loads(r['refs'])
        r['result'] = json.loads(r['payload']) if r['payload'] else {}
        r['prediction'] = r['result'].get('prediction') or {}
    baseline = {r['sample_id']: r for r in rows if r['experiment'] == 'full_multi' and r['state'] == 'done'}
    confidence_bins, quality_groups = defaultdict(list), defaultdict(list)
    logical_flags = []
    for r in baseline.values():
        p = r['prediction']
        if r['refs']:
            confidence_bins[min(9, int(p['confidence'] * 10))].append(r)
        geo = assets[r['asset_id']]['geometry']
        if r['refs']:
            quality_groups['watertight' if geo['watertight'] else 'not_watertight'].append(r)
            quality_groups['volume_valid' if geo['volume_source_units_cubed'] is not None else 'volume_unavailable'].append(r)
            n = geo['triangles']
            quality_groups['triangles_lt_1k' if n < 1000 else 'triangles_1k_to_10k' if n < 10000 else 'triangles_10k_to_100k' if n < 100000 else 'triangles_ge_100k'].append(r)
        uncertainty = ' '.join(p.get('uncertainties', []))
        if not p.get('needs_context') and any(s in uncertainty for s in ('需上下文', '需要装配', '无法确定主要功能', '无法确认实际用途', '无法确定实际用途')):
            logical_flags.append({'sample_id': r['sample_id'], 'reason': 'needs_context=false conflicts with context-related uncertainty text', 'uncertainties': p['uncertainties']})
    def describe(group):
        return {'n': len(group), 'weak_reference_match_rate': sum(r['prediction'].get('function_code') in r['refs'] for r in group) / len(group),
                'auto_accept_coverage': sum(r['result'].get('status') == 'accepted' for r in group) / len(group)}
    confidence = [{'score_interval': f'{i/10:.1f}–{(i+1)/10:.1f}', **describe(group)} for i, group in sorted(confidence_bins.items())]
    quality = {key: describe(group) for key, group in quality_groups.items()}
    paired = {}
    for exp in sorted({r['experiment'] for r in rows} - {'full_multi', 'contextless_probe'}):
        strata = defaultdict(list)
        for r in rows:
            if r['experiment'] != exp or r['state'] != 'done' or r['sample_id'] not in baseline or not r['refs']:
                continue
            a = baseline[r['sample_id']]['result']['agents'][0]['function_code']
            b = r['prediction']['function_code']
            strata[r['source']].append(int(b in r['refs']) - int(a in r['refs']))
        means = np.array([np.mean(v) for v in strata.values()])
        if len(means):
            rng = np.random.default_rng(20261016)
            boot = means[rng.integers(0, len(means), size=(1000, len(means)))].mean(axis=1)
            paired[exp] = {'n_paired': sum(map(len, strata.values())), 'n_source_strata': len(strata),
                           'source_macro_reference_delta': float(means.mean()),
                           'source_cluster_bootstrap_95pct_interval': np.quantile(boot, [.025, .975]).tolist(),
                           'warning': 'Exploratory resampling of source strata on weak labels, not proof of true accuracy improvement.'}
    integrity = {'checked': False}
    previous_path = root / 'supplementary_analysis.json'
    if not verify_inputs and previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding='utf-8'))
        if previous.get('input_integrity', {}).get('checked'):
            integrity = dict(previous['input_integrity'])
            integrity.setdefault('checked_at', previous.get('at'))
            integrity['reused_prior_check'] = True
    if verify_inputs:
        changed, unreadable = [], []
        for s in inputs:
            try:
                actual = file_hash(Path(s['path']))
                if actual != s['sha']:
                    changed.append({'sample_id': s['id'], 'path': s['path'], 'expected_sha': s['sha'], 'actual_sha': actual})
            except OSError as exc:
                unreadable.append({'sample_id': s['id'], 'path': s['path'], 'error': str(exc)})
        integrity = {'checked': True, 'checked_at': now(), 'n_inputs': len(inputs), 'unchanged': len(inputs)-len(changed)-len(unreadable), 'changed': changed, 'unreadable': unreadable}
    error_details = [{'job_id': r['id'], 'sample_id': r['sample_id'], 'experiment': r['experiment'], 'source_stratum': r['source'],
                      'error': r['error'], 'synthetic': r['synthetic']} for r in rows if r['state'] == 'error']
    role_latency = {}
    for role in {r['role'] for r in requests}:
        lat = [r['seconds'] for r in requests if r['role'] == role and r['seconds'] is not None]
        if lat:
            role_latency[role] = {'n_attempts_with_latency': len(lat), 'median_seconds': float(np.median(lat)), 'p95_seconds': float(np.quantile(lat, .95))}
    result = {'at': now(), 'complete': all(r['state'] in {'done', 'error'} for r in rows),
              'confidence_bins_weak_reference_only': confidence, 'geometry_quality_subgroups': quality,
              'paired_exploratory_intervals': paired, 'heuristic_context_consistency_flags': logical_flags,
              'input_integrity': integrity, 'error_details': error_details, 'api_latency_by_role': role_latency,
              'limits': ['Confidence bins compare weak references, not calibrated probabilities.',
                         'Geometry groups differ in function composition; descriptive associations are not causal effects.',
                         'Context-text flags are heuristic review suggestions, not verified semantic errors.',
                         'Bootstrap intervals use at most 32 source clusters and are exploratory; no production accuracy claim.']}
    atomic_json(root / 'supplementary_analysis.json', result)
    write_controlled_gallery(root, rows, assets)
    lines = ['# 补充诊断', '', f"更新时间：{result['at']}；全计划是否结束：{result['complete']}。", '',
             '## 分数分箱（弱参考，非真实概率校准）', '', '| 分数区间 | 样本数 | 参考匹配率 | 自动接受覆盖率 |', '|---|---:|---:|---:|']
    lines += [f"| {x['score_interval']} | {x['n']} | {x['weak_reference_match_rate']:.1%} | {x['auto_accept_coverage']:.1%} |" for x in confidence]
    lines += ['', '## 几何质量分组（描述性关联）', '', '| 组 | 样本数 | 参考匹配率 | 自动接受覆盖率 |', '|---|---:|---:|---:|']
    lines += [f"| {k} | {x['n']} | {x['weak_reference_match_rate']:.1%} | {x['auto_accept_coverage']:.1%} |" for k, x in quality.items()]
    lines += ['', '## 逐件配对的来源分层重采样', '',
              '按来源目录为簇做1000次bootstrap，区间只是探索性的弱参考差异描述；不代替真实功能标签，不证明因果提升。', '',
              '| 实验 | 配对数 | 来源簇数 | 宏平均参考差异 | 95%探索区间 |', '|---|---:|---:|---:|---|']
    for exp, x in paired.items():
        lo, hi = x['source_cluster_bootstrap_95pct_interval']
        lines.append(f"| {exp} | {x['n_paired']} | {x['n_source_strata']} | {x['source_macro_reference_delta']:+.1%} | [{lo:+.1%}, {hi:+.1%}] |")
    lines += ['', f"功能上下文描述启发式疑点：{len(logical_flags)}件；仅作人工复核提示。", '',
              f"输入文件终检：{integrity}", '', f"明确失败任务：{len(error_details)}项，逐项见supplementary_analysis.json（包含预期无效输入）。", '',
              '[逐模型对照实验画廊](controlled_gallery.html)可查看各输入变体及输出；待执行卡片不包含模型预测。', '',
              '人工标签如已填写，可用evaluate_benchmark_human.py另行评估；原始预测不被覆盖。']
    (root / 'supplementary_analysis.md').write_text('\n'.join(lines), encoding='utf-8')
    return result


def write_controlled_gallery(root, rows, assets):
    from benchmark_store import VARIANTS
    data = defaultdict(list)
    for r in rows:
        if not r['cohort']:
            continue
        p = r['prediction']
        if r['experiment'] == 'full_multi' and r['result'].get('agents'):
            p = r['result']['agents'][0]
        asset = assets.get(r['asset_id'], {})
        data[r['sample_id']].append({'method': '初判基线（8视图+几何）' if r['experiment'] == 'full_multi' else VARIANTS[r['experiment']]['label'],
                                     'function': p.get('function_name', '尚无有效预测'), 'state': r['state'],
                                     'statement': p.get('function_statement', r.get('error') or ''),
                                     'score': p.get('confidence'), 'image': asset.get('image')})
    encoded = json.dumps(dict(sorted(data.items())), ensure_ascii=False).replace('<', chr(92) + 'u003c')
    page = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>逐模型对照实验</title>
<style>body{font:16px system-ui;background:#f1f5f9;color:#123;margin:24px}header{position:sticky;top:0;background:#f1f5f9;padding:10px}#cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:16px}article{background:white;padding:18px;border-radius:12px}img{width:100%}select{padding:10px}</style>
<header><h1>同一模型的多种对照实验</h1><p>生成时间：GENERATED_AT。本页是补充诊断快照，完成后自动重建；实时进度见report.md。基线使用首个agent；点击图片查看原尺寸，执行中卡片不代表已有预测。</p><select id="sample"></select></header><main id="cards"></main>
<script>const data=DATA;const select=document.getElementById('sample');for(const id of Object.keys(data)){const o=document.createElement('option');o.value=id;o.textContent=id;select.append(o)}function draw(){const cards=document.getElementById('cards');cards.replaceChildren();for(const r of data[select.value]||[]){const a=document.createElement('article');const h=document.createElement('h2');h.textContent=r.method;a.append(h);const p=document.createElement('p');p.textContent=r.function+' · '+r.state+' · 分数 '+r.score;a.append(p);const t=document.createElement('p');t.textContent=r.statement;a.append(t);if(r.image){const link=document.createElement('a');link.href=r.image;link.target='_blank';const im=document.createElement('img');im.loading='lazy';im.src=r.image;link.append(im);a.append(link)}cards.append(a)}}select.onchange=draw;draw();</script></html>'''
    (Path(root) / 'controlled_gallery.html').write_text(page.replace('GENERATED_AT', now()).replace('DATA', encoded), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--skip-input-check', action='store_true')
    args = parser.parse_args()
    result = analyze(args.root, verify_inputs=not args.skip_input_check)
    print(json.dumps({'complete': result['complete'], 'input_integrity': result['input_integrity'],
                      'n_error_tasks': len(result['error_details'])}, ensure_ascii=True, indent=2))
