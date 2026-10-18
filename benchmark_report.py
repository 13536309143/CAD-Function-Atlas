"""Detailed partial/final reports; pending work is never counted as a prediction."""
from __future__ import annotations
from collections import Counter, defaultdict
import csv
import html
import json
from pathlib import Path
import statistics
import time

from benchmark_store import connect, atomic_json, now, VARIANTS
from cad_functions import FUNCTIONS, acceptance_issues


def ratio(a, b):
    return a / b if b else None


def percent(value):
    return f'{value:.1%}' if value is not None else '—'


def generate_report(root, final=False):
    root = Path(root)
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    db = connect(root)
    rows = [dict(r) for r in db.execute('''SELECT j.*,s.path,s.source,s.sha,s.refs,s.synthetic,s.cohort
                      FROM jobs j JOIN samples s ON s.id=j.sample_id ORDER BY j.experiment,j.id''')]
    assets = {r['id']: dict(r) for r in db.execute('SELECT * FROM assets')}
    requests = [dict(r) for r in db.execute('SELECT * FROM requests ORDER BY id')]
    raw_state = db.execute("SELECT value FROM state WHERE key='pipeline'").fetchone()
    state = json.loads(raw_state[0]) if raw_state else {}
    inputs = [dict(r) for r in db.execute('SELECT * FROM samples WHERE synthetic=0')]
    db.close()
    by_experiment = defaultdict(list)
    parsed_assets = {}
    def asset_payload(aid):
        if aid not in parsed_assets:
            parsed_assets[aid] = json.loads(assets[aid]['payload']) if assets[aid].get('payload') else {}
        return parsed_assets[aid]
    for r in rows:
        r['refs'] = json.loads(r['refs'])
        r['result'] = json.loads(r['payload']) if r.get('payload') else {}
        r['prediction'] = r['result'].get('prediction') or {}
        by_experiment[r['experiment']].append(r)
    experiments, source_breakdown, matrix = {}, [], Counter()
    for exp, group in by_experiment.items():
        terminal = [r for r in group if r['state'] in {'done', 'error'}]
        evaluated = [r for r in terminal if r['refs']]
        matched = lambda r: r['state'] == 'done' and r['prediction'].get('function_code') != 'unknown' and r['prediction'].get('function_code') in r['refs']
        source_groups = defaultdict(list)
        for r in evaluated:
            source_groups[r['source']].append(r)
        accepted = [r for r in evaluated if r['result'].get('status') == 'accepted']
        metric = {
            'label': VARIANTS[exp]['label'], 'scheduled': len(group), 'terminal': len(terminal),
            'inference_completed': sum(r['state'] == 'done' for r in group),
            'errors': sum(r['state'] == 'error' for r in group),
            'pending_or_running': sum(r['state'] in {'pending', 'running'} for r in group),
            'reference_evaluable_terminal': len(evaluated), 'reference_matches': sum(matched(r) for r in evaluated),
            'weak_reference_match_rate': ratio(sum(matched(r) for r in evaluated), len(evaluated)),
            'source_macro_reference_match_rate': statistics.mean(sum(matched(r) for r in g) / len(g) for g in source_groups.values()) if source_groups else None,
            'source_strata_evaluated': len(source_groups),
            'auto_accept_coverage': ratio(sum(r['result'].get('status') == 'accepted' for r in terminal), len(terminal)),
            'accepted_reference_match_rate': ratio(sum(matched(r) for r in accepted), len(accepted)),
            'unknown_rate_completed': ratio(sum(r['prediction'].get('function_code') == 'unknown' for r in terminal), len(terminal)),
            'status_counts': dict(Counter(r['result'].get('status', r['state']) for r in group)),
            'function_counts': dict(Counter(r['prediction'].get('function_code', r['state']) for r in terminal)),
        }
        experiments[exp] = metric
        if exp == 'full_multi':
            for source, g in source_groups.items():
                source_breakdown.append({'source_stratum': source, 'n_terminal': len(g),
                                         'reference_match_rate': sum(matched(r) for r in g) / len(g),
                                         'n_accepted': sum(r['result'].get('status') == 'accepted' for r in g),
                                         'n_unknown': sum(r['prediction'].get('function_code') == 'unknown' for r in g)})
            for r in evaluated:
                if len(r['refs']) == 1:
                    matrix[(r['refs'][0], r['prediction'].get('function_code', 'error'))] += 1
    main = by_experiment.get('full_multi', [])
    main_done = [r for r in main if r['state'] == 'done']
    full_lookup = {r['sample_id']: r for r in main_done}
    baseline_rows = []
    issues = Counter()
    change_counts = Counter()
    for r in main_done:
        agents = r['result'].get('agents') or []
        if agents:
            baseline_rows.append((r, agents[0]))
        if len(agents) >= 2:
            change_counts['independent_pair_agree' if agents[0]['function_code'] == agents[1]['function_code'] else 'independent_pair_disagree'] += 1
        issues.update(r['result'].get('review_issues', []))
        if agents and r['prediction']['function_code'] != agents[0]['function_code']:
            before = agents[0]['function_code'] in r['refs'] if r['refs'] else None
            after = r['prediction']['function_code'] in r['refs'] if r['refs'] else None
            change_counts['final_changed_initial'] += 1
            if before is not None:
                change_counts['reference_gain' if after and not before else 'reference_loss' if before and not after else 'changed_no_reference_gain'] += 1
    eligible_baseline = [(r, p) for r, p in baseline_rows if r['refs']]
    baseline = {'n_predictions_available': len(baseline_rows), 'n_reference_evaluable': len(eligible_baseline),
                'weak_reference_match_rate': ratio(sum(p['function_code'] != 'unknown' and p['function_code'] in r['refs'] for r, p in eligible_baseline), len(eligible_baseline)),
                'paired_multi_reference_match_rate': ratio(sum(r['prediction']['function_code'] != 'unknown' and r['prediction']['function_code'] in r['refs'] for r, p in eligible_baseline), len(eligible_baseline)),
                'comparison_note': 'Derived from the initial agent in the SAME full test; no extra requests. Main error cases without initial predictions are excluded here, disclosed by n_predictions_available.'}
    comparisons = {}
    for exp, group in by_experiment.items():
        if exp in {'full_multi', 'contextless_probe'}:
            continue
        pairs = [(r, full_lookup[r['sample_id']]['result']['agents'][0]) for r in group if r['state'] == 'done' and r['sample_id'] in full_lookup]
        scored = [(r, p) for r, p in pairs if r['refs']]
        comparisons[exp] = {
            'n_paired': len(pairs),
            'primary_function_agreement_with_initial_baseline': ratio(sum(r['prediction']['function_code'] == p['function_code'] for r, p in pairs), len(pairs)),
            'n_reference_evaluable_paired': len(scored),
            'paired_baseline_reference_rate': ratio(sum(p['function_code'] in r['refs'] and p['function_code'] != 'unknown' for r, p in scored), len(scored)),
            'paired_variant_reference_rate': ratio(sum(r['prediction']['function_code'] in r['refs'] and r['prediction']['function_code'] != 'unknown' for r, p in scored), len(scored)),
            'reference_gains': sum(r['prediction']['function_code'] in r['refs'] and p['function_code'] not in r['refs'] for r, p in scored),
            'reference_losses': sum(p['function_code'] in r['refs'] and r['prediction']['function_code'] not in r['refs'] for r, p in scored),
        }
    repeat_lookup = {exp: {r['sample_id']: r for r in by_experiment.get(exp, []) if r['state'] == 'done'} for exp in ('repeat_1', 'repeat_2')}
    triples = []
    for sid, r in repeat_lookup['repeat_1'].items():
        if sid in repeat_lookup['repeat_2'] and sid in full_lookup:
            triples.append([full_lookup[sid]['result']['agents'][0]['function_code'], r['prediction']['function_code'], repeat_lookup['repeat_2'][sid]['prediction']['function_code']])
    repeatability = {'n_complete_triplets': len(triples),
                     'three_run_unanimity': ratio(sum(len(set(t)) == 1 for t in triples), len(triples)),
                     'mean_pairwise_agreement': statistics.mean(sum(t[i] == t[j] for i, j in ((0, 1), (0, 2), (1, 2))) / 3 for t in triples) if triples else None,
                     'note': 'Fresh requests with identical prompts/images. Unknown-to-unknown agreement does not prove a useful classification.'}
    threshold_sweep = []
    for threshold in (.4, .5, .55, .6, .65, .7, .75, .8, .85, .9, .95):
        accepted_rows = []
        for r in main_done:
            agents = r['result'].get('agents', [])
            if len(agents) >= 2 and not acceptance_issues(agents[:2], asset_payload(r['asset_id'])['geometry'], threshold):
                accepted_rows.append((r, agents[0]))
        scored = [(r, p) for r, p in accepted_rows if r['refs']]
        threshold_sweep.append({'threshold': threshold, 'accepted': len(accepted_rows),
                                'coverage_of_completed': ratio(len(accepted_rows), len(main_done)),
                                'accepted_reference_match_rate': ratio(sum(p['function_code'] in r['refs'] for r, p in scored), len(scored))})
    health = Counter()
    format_checks = []
    for asset in assets.values():
        if asset['state'] != 'ready' or asset['variant'] != 'base':
            continue
        geo = asset_payload(asset['id'])['geometry']
        health['unique_ready_base_assets'] += 1
        health['watertight' if geo['watertight'] else 'not_watertight'] += 1
        health['valid_volume' if geo['volume_source_units_cubed'] is not None else 'volume_unavailable'] += 1
        health['with_duplicate_faces_removed'] += bool(geo['duplicate_faces_removed'])
        health['with_invalid_elements_removed'] += bool(geo['invalid_vertices_removed'] or geo['invalid_or_degenerate_faces_removed'])
        health['multiple_components'] += geo['connected_components'] > 1
    for r in rows:
        if r['experiment'] not in {'format_stl', 'format_glb', 'scale_translate', 'rotate_a', 'rotate_b'} or r['sample_id'] not in full_lookup:
            continue
        baseline_asset = asset_payload(full_lookup[r['sample_id']]['asset_id'])
        current = asset_payload(r['asset_id'])
        if not current or not baseline_asset:
            continue
        a, b = baseline_asset['geometry'], current['geometry']
        factor = 17 if r['experiment'] == 'scale_translate' else 1
        area_error = abs(b['area_source_units_squared'] / (a['area_source_units_squared'] * factor**2) - 1) if a['area_source_units_squared'] else None
        format_checks.append({'sample_id': r['sample_id'], 'experiment': r['experiment'], 'area_relative_error': area_error,
                              'euler_same': a['euler_number'] == b['euler_number'],
                              'watertight_same': a['watertight'] == b['watertight'],
                              'triangle_delta': b['triangles'] - a['triangles'],
                              'conversion_note': current.get('conversion_note')})
    duplicate_groups = defaultdict(list)
    for s in inputs:
        duplicate_groups[s['sha']].append(s)
    duplicates = [{'sha256': sha, 'files': [x['id'] for x in group], 'source_strata': sorted({x['source'] for x in group}),
                   'reference_sets_differ': len({x['refs'] for x in group}) > 1} for sha, group in duplicate_groups.items() if len(group) > 1]
    progress = {'source_files_planned': len(main), 'full_inference_done': len(main_done),
                'full_errors': sum(r['state'] == 'error' for r in main),
                'full_pending_running': sum(r['state'] in {'pending', 'running'} for r in main),
                'all_jobs_planned': len(rows), 'all_jobs_done': sum(r['state'] == 'done' for r in rows),
                'all_jobs_errors': sum(r['state'] == 'error' for r in rows),
                'assets_planned': len(assets), 'assets_ready': sum(a['state'] == 'ready' for a in assets.values()),
                'assets_errors': sum(a['state'] == 'error' for a in assets.values())}
    latencies = sorted(r['seconds'] for r in requests if r['seconds'] is not None)
    probes = [r for r in by_experiment.get('contextless_probe', []) if r['state'] in {'done', 'error'}]
    invalid = [r for r in probes if r['source'] == 'synthetic_invalid_geometry']
    contextless = [r for r in probes if r['source'] != 'synthetic_invalid_geometry' and r['state'] == 'done']
    all_terminal = all(r['state'] in {'done', 'error'} for r in rows)
    summary = {'updated': now(), 'complete': all_terminal, 'runner_state': state, 'progress': progress,
               'api_calls': len(requests), 'request_states': dict(Counter(r['state'] for r in requests)),
               'additional_request_attempts': sum(r['attempt'] > 0 for r in requests),
               'api_latency_seconds': {'median': statistics.median(latencies) if latencies else None,
                                       'p95': latencies[min(len(latencies)-1, int(len(latencies)*.95))] if latencies else None,
                                       'sum': sum(latencies)},
               'experiments': experiments, 'single_agent_baseline': baseline,
               'multi_agent_changes': dict(change_counts), 'review_reason_counts': dict(issues),
               'paired_comparisons': comparisons, 'repeatability': repeatability,
               'geometry_health_unique_assets_including_synthetic': dict(health),
               'exact_duplicate_groups': len(duplicates), 'exact_duplicate_extra_files': sum(len(g['files'])-1 for g in duplicates),
               'synthetic_probes': {'invalid_terminal': len(invalid), 'invalid_rejected_before_api': sum(r['state'] == 'error' for r in invalid),
                                     'contextless_completed': len(contextless),
                                     'conservative_flag_rate': ratio(sum(r['result'].get('status') != 'accepted' or r['prediction'].get('needs_context') for r in contextless), len(contextless)),
                                     'note': 'Contextless primitives are ambiguity probes, not expert-labelled real out-of-domain objects.'},
               'human_verified_accuracy': None,
               'limitations': ['All performance labels are provisional directory-derived references.',
                              'Pending jobs have no predictions and are not silently treated as completed.',
                              'Unknown/error terminal jobs stay in reference evaluation denominators.',
                              'Multiple functional reference choices are allowed where frozen before the run.',
                              'Consistency tests conflate input perturbation effects and model stochasticity; repetitions provide context.',
                              'Format export flattens scene metadata and can alter floating-point precision.',
                              'No monetary cost estimate: VLM endpoint does not report per-request token billing.']}
    atomic_json(root / 'summary.json', summary)
    atomic_json(root / 'progress.json', {'updated': now(), **progress, 'api_calls': len(requests), 'runner_state': state})
    atomic_json(root / 'threshold_sweep.json', threshold_sweep)
    atomic_json(root / 'format_geometry_checks.json', format_checks)
    atomic_json(root / 'duplicate_inputs.json', duplicates)
    atomic_json(root / 'source_breakdown.json', source_breakdown)
    atomic_json(root / 'confusion_single_reference_only.json', [{'reference': a, 'predicted': b, 'n': n} for (a, b), n in matrix.items()])
    export_tables(root, rows, source_breakdown)
    make_gallery(root, main, complete=all_terminal)
    if final or not (root / 'figures' / 'experiment_comparison.png').exists() or time.time() - (root / 'figures' / 'experiment_comparison.png').stat().st_mtime > 300:
        figure_experiments = experiments
        if (root / 'FROZEN_RELEASE.json').exists():
            figure_experiments = {k: {**v, 'scheduled': v['terminal']} for k, v in experiments.items()}
        make_figures(root, figure_experiments, matrix, threshold_sweep)
    lines = ['# CAD 全量功能分类与多种对照测试', '',
             f"更新时间：{summary['updated']}；状态：{'全部计划任务已结束（含明确记录的失败）' if all_terminal else '执行中或被资源条件阻挡，以下均为阶段结果'}。", '',
             f"全量主测试：计划{len(main)}件，推理完成{len(main_done)}件，失败{progress['full_errors']}件，待执行/运行中{progress['full_pending_running']}件。", '',
             f"全部实验：计划{len(rows)}项，推理完成{progress['all_jobs_done']}项，失败{progress['all_jobs_errors']}项。API实际尝试记录{len(requests)}次（含失败、修复和进程中断时无法确认完成的请求）。", '',
             '**没有人工功能真值，本报告不声称测得真实功能准确率。待执行样本不参与已完成样本的匹配率，完成率单独报告。**', '',
             f"运行器状态：`{state.get('status')}`；原因：{state.get('reason') or '无'}。", '',
             '## 试验矩阵与当前结果', '',
             '| 实验 | 计划 | 推理完成 | 失败 | 已结束样本参考匹配率 | 来源分层宏平均 |', '|---|---:|---:|---:|---:|---:|']
    for exp, metric in experiments.items():
        lines.append(f"| {metric['label']} | {metric['scheduled']} | {metric['inference_completed']} | {metric['errors']} | {percent(metric['weak_reference_match_rate'])} | {percent(metric['source_macro_reference_match_rate'])} |")
    lines += ['', '## 单agent与多agent', '',
              f"主测试首个agent已有{baseline['n_predictions_available']}个输出，弱参考匹配率{percent(baseline['weak_reference_match_rate'])}。同批多agent结果见上表；比较时检查可评估样本数是否一致。", '',
              f"判断变化：`{dict(change_counts)}`；复核触发原因可重叠：`{dict(issues)}`。", '',
              '## 对照与稳定性（配对样本）', '',
              '| 实验 | 配对数 | 与初判功能一致率 | 同批初判参考匹配 | 同批变体参考匹配 | 参考增益/损失 |', '|---|---:|---:|---:|---:|---|']
    for exp, metric in comparisons.items():
        lines.append(f"| {VARIANTS[exp]['label']} | {metric['n_paired']} | {percent(metric['primary_function_agreement_with_initial_baseline'])} | {percent(metric['paired_baseline_reference_rate'])} | {percent(metric['paired_variant_reference_rate'])} | {metric['reference_gains']}/{metric['reference_losses']} |")
    lines += ['', f"三次重复输入完整配对{len(triples)}组；三次一致率{percent(repeatability['three_run_unanimity'])}，平均两两一致率{percent(repeatability['mean_pairwise_agreement'])}。unknown一致不等于识别正确。", '',
              '## 数据与几何质量', '',
              f"精确重复文件组{len(duplicates)}组，额外重复文件{summary['exact_duplicate_extra_files']}个。完整重复组及来源参考冲突见duplicate_inputs.json。", '',
              f"按去重基础渲染资产统计（含合成探针）：`{dict(health)}`。全部格式及变换的面积、拓扑变化见format_geometry_checks.json。", '',
              '32个来源目录数量严重不均衡，因此同时报告按文件微平均和按来源目录宏平均。宏平均来源是原目录分层，并非已确认的功能类别。无可靠参考的通用形状目录保留预测，但不强行赋予功能真值。', '',
              '## 合成探针与失败处理', '',
              f"无效几何探针已结束{len(invalid)}件，其中预处理拒绝{summary['synthetic_probes']['invalid_rejected_before_api']}件；这是输入检查结果。无上下文合成探针已有{len(contextless)}件推理结果，保守标记比例{percent(summary['synthetic_probes']['conservative_flag_rate'])}。", '',
              '这些合成形状不是经过人工标注的真实域外零件，不能将保守标记比例称为开放集拒识准确率。HTTP失败、格式修复、输入失败均保留；额度/认证阻挡不会制造一万个分类失败结果。', '',
              '## 预算、进度和复现', '',
              f"请求预算{config['max_calls']}次，API并发{config['api_workers']}，启动速率上限{config['max_request_starts_per_second']}次/秒。配额查询见latest_quota.json。实际货币花费未知，不从请求次数编造费用。", '',
              '数据库benchmark.sqlite保存任务、请求和状态；requests/保存匿名输入提示词、图像散列、响应与耗时；source_snapshot/保存本轮源程序和依赖版本；config.json冻结功能体系与实验配置，manifest.json冻结输入SHA。重复实验有独立请求，不复用模型回答。原始data只读。', '',
              '完整清单：[classification.csv](classification.csv)；全量交互查看：[gallery.html](gallery.html)；统计：[summary.json](summary.json)；待人工确认：[review_queue.csv](review_queue.csv)。', '',
              '## 图表', '',
              '![实验完成与参考匹配](figures/experiment_comparison.png)', '',
              '![单参考混淆计数](figures/confusion.png)', '',
              '![单参考混淆行归一化](figures/confusion_row_normalized.png)', '',
              '![阈值回放](figures/threshold_sweep.png)', '',
              '接口限流依据：[MiniMax官方Token Plan说明](https://platform.minimaxi.com/subscribe/coding-plan)。']
    if (root / 'supplementary_analysis.md').exists():
        lines += ['', '## 补充诊断', '', '[分数分箱、几何分组、配对bootstrap区间及输入SHA终检](supplementary_analysis.md)。']
    if (root / 'human_evaluation.json').exists():
        human = json.loads((root / 'human_evaluation.json').read_text(encoding='utf-8'))
        lines += ['', '## 已提供人工标签的子集', '',
                  f"标注{human['n_labels']}件，可评估已结束任务{human['n_evaluated_terminal']}件，人工标签子集准确率{percent(human['human_accuracy_on_labelled_terminal_subset'])}。仅适用于该子集，详见[人工评估](human_evaluation.json)。"]
    if (root / 'FROZEN_RELEASE.json').exists():
        dataset_size = experiments['full_multi']['terminal']
        lines[2] = (f"更新时间：{summary['updated']}；状态：已封存，不再自动恢复。"
                    f"数据集包含 {dataset_size:,} 个模型；成功输出与执行失败分别统计。")
        lines[4] = f"数据集：{dataset_size:,} 个模型，成功输出 {len(main_done):,} 件，执行失败 {progress['full_errors']:,} 件。"
        lines[6] = (f"全部实验：已结束 {progress['all_jobs_done'] + progress['all_jobs_errors']:,} 项，"
                    f"成功输出 {progress['all_jobs_done']:,} 项，执行失败 {progress['all_jobs_errors']:,} 项。"
                    f"API 实际尝试记录 {len(requests):,} 次。")
        lines[8] = '**没有人工功能真值，本报告不声称测得真实功能准确率。参考匹配率按实际可评估样本计算，执行失败单独记录。**'
        lines = [line.replace('| 实验 | 计划 |', '| 实验 | 样本数 |')
                 .replace(f"| {experiments['full_multi']['label']} | {experiments['full_multi']['scheduled']} |",
                          f"| {experiments['full_multi']['label']} | {dataset_size} |") for line in lines]
        lines[3:3] = ['', '完整分析：[中文实验报告](../../docs/EXPERIMENT_REPORT.zh-CN.md) · [English report](../../docs/EXPERIMENT_REPORT.md)', '']
    (root / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return summary


def export_tables(root, rows, source_breakdown):
    fields = ['sample_id', 'experiment', 'execution_state', 'decision_status', 'primary_function', 'secondary_functions',
              'function_statement', 'confidence', 'needs_context', 'reference_functions', 'reference_match', 'source_path', 'image', 'error']
    with (root / 'classification.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            p = r['prediction']
            writer.writerow({'sample_id': r['sample_id'], 'experiment': r['experiment'], 'execution_state': r['state'],
                             'decision_status': r['result'].get('status', ''), 'primary_function': p.get('function_name', ''),
                             'secondary_functions': '；'.join(FUNCTIONS[c]['name'] for c in p.get('secondary_functions', [])),
                             'function_statement': p.get('function_statement', ''), 'confidence': p.get('confidence', ''),
                             'needs_context': p.get('needs_context', ''), 'reference_functions': '；'.join(FUNCTIONS[c]['name'] for c in r['refs']),
                             'reference_match': p.get('function_code') in r['refs'] if r['refs'] and r['state'] in {'done', 'error'} else '',
                             'source_path': r['path'], 'image': r['result'].get('image', ''), 'error': r.get('error') or ''})
    with (root / 'review_queue.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['sample_id', 'proposed_function_code', 'status', 'reference_match', 'views', 'confirmed_function_code', 'reviewer_note'])
        for r in rows:
            if r['experiment'] == 'full_multi' and r['state'] in {'done', 'error'}:
                writer.writerow([r['sample_id'], r['prediction'].get('function_code', ''), r['result'].get('status', 'error'),
                                 r['prediction'].get('function_code') in r['refs'] if r['refs'] else '', r['result'].get('image', ''), '', ''])
    folder = root / 'organized_index'
    folder.mkdir(exist_ok=True)
    groups = defaultdict(list)
    for r in rows:
        if r['experiment'] == 'full_multi' and r['state'] == 'done':
            groups[r['prediction']['function_code']].append(r)
    for code, group in groups.items():
        with (folder / (code + '.csv')).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['sample_id', 'function', 'status', 'source_path'])
            writer.writerows([r['sample_id'], FUNCTIONS[code]['name'], r['result']['status'], r['path']] for r in group)


def make_gallery(root, main, complete):
    data = []
    for r in main:
        p = r['prediction']
        if r['state'] not in {'done', 'error'}:
            continue
        data.append({'id': r['sample_id'], 'function': p.get('function_name', '处理失败'),
                     'status': r['result'].get('status', 'error'), 'score': p.get('confidence'),
                     'statement': p.get('function_statement', r.get('error', '')),
                     'reference_match': p.get('function_code') in r['refs'] if r['refs'] else None,
                     'image': r['result'].get('image', ''), 'detail': 'jobs/' + r['id'] + '/result.json'})
    encoded = json.dumps(data, ensure_ascii=False).replace('<', chr(92) + 'u003c')
    page = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>全量功能分类</title>
<style>body{font:16px system-ui;margin:24px;background:#f1f5f9;color:#123}header{position:sticky;top:0;background:#f1f5f9;padding:12px}button,select,input{padding:8px;margin:4px}article{background:white;padding:20px;margin:20px 0;border-radius:12px}img{width:100%;max-width:1440px}small{color:#567}</style>
<header><h1>CAD 全量功能分类</h1><p>REPORT_STATE · 待复核是建议功能，不代表已确认用途。</p><input id="q" placeholder="模型ID或功能"><select id="s"><option value="">全部状态</option><option>accepted</option><option>review_required</option><option>unknown</option><option>error</option></select><label><input type="checkbox" id="m">仅显示与弱参考不匹配</label><button id="prev">上一页</button><button id="next">下一页</button><span id="count"></span></header><main id="list"></main>
<script>const rows=DATA;let page=0;const $=s=>document.getElementById(s);function draw(){let q=$('q').value.trim().toLowerCase();let filtered=rows.filter(r=>(!q||(r.id+' '+r.function).toLowerCase().includes(q))&&(!$('s').value||r.status===$('s').value)&&(!$('m').checked||r.reference_match===false));let pages=Math.max(1,Math.ceil(filtered.length/20));page=Math.max(0,Math.min(page,pages-1));$('list').replaceChildren();for(const r of filtered.slice(page*20,page*20+20)){const a=document.createElement('article');const h=document.createElement('h2');h.textContent=r.id+' · '+r.function;a.append(h);const p=document.createElement('p');p.textContent=r.status+' · 分数 '+r.score+' · '+r.statement;a.append(p);if(r.image){const im=document.createElement('img');im.loading='lazy';im.src=r.image;a.append(im)}const link=document.createElement('a');link.href=r.detail;link.textContent='查看原始判断链';a.append(link);$('list').append(a)}$('count').textContent=filtered.length+'件 / 第'+(page+1)+'页，共'+pages+'页'}for(const id of ['q','s','m'])$(id).addEventListener('input',()=>{page=0;draw()});$('prev').onclick=()=>{page--;draw()};$('next').onclick=()=>{page++;draw()};draw();</script></html>'''
    page = page.replace('REPORT_STATE', '全计划已结束' if complete else '阶段结果，任务未全部结束').replace('DATA', encoded)
    (root / 'gallery.html').write_text(page, encoding='utf-8')


def make_figures(root, experiments, matrix, thresholds):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    folder = root / 'figures'
    folder.mkdir(exist_ok=True)
    names = list(experiments)
    fig, ax = plt.subplots(figsize=(12, 7))
    ys = range(len(names))
    values = [experiments[k]['weak_reference_match_rate'] for k in names]
    ax.barh(list(ys), [v if v is not None else 0 for v in values], color='#397aa8')
    ax.set_yticks(list(ys), [f"{k} (done {experiments[k]['terminal']}/{experiments[k]['scheduled']})" for k in names])
    ax.set_xlim(0, 1)
    ax.set_xlabel('Weak reference match rate on terminal jobs (not human accuracy)')
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(folder / 'experiment_comparison.png', dpi=150)
    plt.close(fig)
    codes = list(FUNCTIONS) + ['error']
    counts = np.zeros((len(codes), len(codes)), dtype=int)
    for (a, b), n in matrix.items():
        if a in codes and b in codes:
            counts[codes.index(a), codes.index(b)] += n
    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(counts, cmap='Blues')
    ax.set_xticks(range(len(codes)), codes, rotation=65, ha='right')
    ax.set_yticks(range(len(codes)), codes)
    ax.set_title('Full-test counts: single weak-reference samples only')
    ax.set_xlabel('Predicted function')
    ax.set_ylabel('Directory-derived reference')
    for i, j in zip(*np.nonzero(counts)):
        ax.text(j, i, str(counts[i, j]), ha='center', va='center', fontsize=8)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(folder / 'confusion.png', dpi=150)
    plt.close(fig)
    row_sums = counts.sum(axis=1, keepdims=True)
    normalized = np.divide(counts, row_sums, out=np.zeros_like(counts, dtype=float), where=row_sums != 0)
    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(normalized, cmap='Blues', vmin=0, vmax=1)
    ax.set_xticks(range(len(codes)), codes, rotation=65, ha='right')
    ax.set_yticks(range(len(codes)), codes)
    ax.set_title('Row-normalized weak-reference confusion (single reference only)')
    for i, j in zip(*np.nonzero(counts)):
        ax.text(j, i, f'{normalized[i,j]:.0%}', ha='center', va='center', fontsize=8)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(folder / 'confusion_row_normalized.png', dpi=150)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot([t['threshold'] for t in thresholds], [t['coverage_of_completed'] or 0 for t in thresholds], 'o-', label='Acceptance coverage')
    ax.plot([t['threshold'] for t in thresholds], [t['accepted_reference_match_rate'] if t['accepted_reference_match_rate'] is not None else np.nan for t in thresholds], 's-', label='Accepted weak-reference match rate')
    ax.set_ylim(0, 1.05)
    ax.set_xlabel('Subjective confidence threshold; other gates retained')
    ax.legend()
    ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(folder / 'threshold_sweep.png', dpi=150)
    plt.close(fig)
