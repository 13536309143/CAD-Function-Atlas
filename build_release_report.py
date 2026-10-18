"""Rebuild publication metrics and bilingual reports from the frozen SQLite snapshot; no API calls."""
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import sqlite3

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from benchmark_report import generate_report
from benchmark_store import now
from cad_functions import FUNCTIONS


ROOT = Path(__file__).resolve().parent


def pct(value):
    return f'{value:.2%}' if value is not None else '—'


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(str(x) for x in row) + ' |' for row in rows])


def build(root):
    root = Path(root)
    docs = ROOT / 'docs'; figures = docs / 'figures'; figures.mkdir(parents=True, exist_ok=True)
    summary = generate_report(root)
    supplement = json.loads((root / 'supplementary_analysis.json').read_text(encoding='utf-8'))
    db = sqlite3.connect(root / 'benchmark.sqlite'); db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute('SELECT j.*,s.source,s.refs,s.synthetic FROM jobs j JOIN samples s ON j.sample_id=s.id')]
    planned = Counter(r[0] for r in db.execute('SELECT source FROM samples WHERE synthetic=0'))
    requests = [dict(r) for r in db.execute('SELECT role,state,seconds,error,started,finished FROM requests')]
    db.close()
    main = [r for r in rows if r['experiment'] == 'full_multi']
    for row in main:
        row['result'] = json.loads(row['payload']) if row['payload'] else {}
        row['prediction'] = row['result'].get('prediction', {})
        row['refs'] = json.loads(row['refs'])
    done = [r for r in main if r['state'] == 'done']
    evaluable = [r for r in done if r['refs']]
    accepted = [r for r in done if r['result'].get('status') == 'accepted']
    paired = summary['single_agent_baseline']
    source_rows = []
    for source, n in sorted(planned.items()):
        group = [r for r in main if r['source'] == source]
        ended = [r for r in group if r['state'] in {'done', 'error'} and r['refs']]
        valid = [r for r in group if r['state'] == 'done' and r['refs']]
        matches = sum(r['prediction'].get('function_code') in r['refs'] for r in valid)
        source_rows.append({'source': source, 'dataset_models': sum(r['state'] in {'done', 'error'} for r in group), 'done': sum(r['state'] == 'done' for r in group),
                            'errors': sum(r['state'] == 'error' for r in group),
                            'reference_n_including_errors': len(ended), 'reference_matches': matches,
                            'reference_match_including_errors': matches / len(ended) if ended else None,
                            'reference_match_successful_only': matches / len(valid) if valid else None})
    errors = Counter(r['error'] for r in main if r['state'] == 'error')
    statuses = Counter(r['result'].get('status') for r in done)
    prediction_counts = Counter(r['prediction'].get('function_code') for r in done)
    full = summary['experiments']['full_multi']
    metrics = {'created': now(), 'release': 'v0.1.0', 'status': 'frozen_dataset_benchmark',
               'dataset_models': full['terminal'], 'successful_models': len(done), 'execution_failures': full['errors'],
               'successful_coverage': len(done) / full['terminal'],
               'retained_attempted_models': full['terminal'], 'reference_agreement_successful_only': paired['paired_multi_reference_match_rate'],
               'reference_agreement_including_execution_failures': full['weak_reference_match_rate'],
               'source_macro_reference_agreement_including_errors': full['source_macro_reference_match_rate'],
               'reference_denominator_successful': len(evaluable), 'reference_matches': full['reference_matches'],
               'reference_denominator_including_errors': full['reference_evaluable_terminal'],
               'single_agent_paired_reference_agreement': paired['weak_reference_match_rate'],
               'multi_agent_paired_delta_percentage_points': 100 * (paired['paired_multi_reference_match_rate'] - paired['weak_reference_match_rate']),
               'auto_accept_count': len(accepted), 'auto_accept_coverage_successful': len(accepted) / len(done),
               'auto_accept_coverage_attempted': full['auto_accept_coverage'],
               'auto_accept_reference_agreement': full['accepted_reference_match_rate'],
               'auto_accept_reference_n': sum(bool(r['refs']) for r in accepted),
               'auto_accept_reference_matches': sum(r['prediction'].get('function_code') in r['refs'] for r in accepted if r['refs']),
               'status_counts': dict(statuses), 'prediction_counts': dict(prediction_counts),
               'execution_error_counts': dict(errors), 'request_attempts': len(requests),
               'request_states': dict(Counter(r['state'] for r in requests)),
               'repeatability': summary['repeatability'], 'paired_controls': summary['paired_comparisons'],
               'geometry_health': summary['geometry_health_unique_assets_including_synthetic'],
               'human_verified_accuracy': None,
               'warning': 'All reported reference agreements use directory-derived weak labels. No human gold labels or true functional accuracy are available.'}
    (docs / 'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    (docs / 'source_metrics.json').write_text(json.dumps(source_rows, ensure_ascii=False, indent=2), encoding='utf-8')
    with (docs / 'source_metrics.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        w = csv.DictWriter(stream, fieldnames=list(source_rows[0]));w.writeheader();w.writerows(source_rows)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False, 'figure.facecolor': 'white'})
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))
    axes[0].bar(['Output', 'Failed'], [len(done), full['errors']], color=['#2563eb', '#e59632'])
    axes[0].set(title=f"Dataset: {full['terminal']:,} models", ylabel='Models')
    rates = [paired['weak_reference_match_rate'], paired['paired_multi_reference_match_rate'], full['accepted_reference_match_rate']]
    bars = axes[1].bar(['Single', 'Multi', 'Accepted\nsubset'], np.array(rates) * 100, color=['#94a3b8', '#2563eb', '#08916c'])
    axes[1].set(title='Weak-reference agreement', ylabel='Percent', ylim=(0, 106))
    axes[1].bar_label(bars, labels=[f'{x:.1%}' for x in rates], padding=3)
    axes[2].bar(['Accept', 'Review', 'Unknown'], [statuses['accepted'], statuses['review_required'], statuses['unknown']], color=['#08916c', '#2563eb', '#94a3b8'])
    axes[2].set(title='Successful-output disposition', ylabel='Models')
    fig.suptitle('CAD Function Atlas · Frozen dataset evaluation', fontsize=15, weight='bold')
    fig.text(.5, .01, 'Weak-reference agreement is not human-verified accuracy. The accepted subset is selected, not a random test set.', ha='center', fontsize=9)
    fig.tight_layout(rect=(0, .05, 1, .94));fig.savefig(figures / 'release_overview.png', dpi=170);plt.close(fig)
    comparable = sorted([r for r in source_rows if r['reference_n_including_errors'] >= 10], key=lambda r: r['reference_match_including_errors'])
    fig, ax = plt.subplots(figsize=(10, 9))
    labels = [f"{r['source']} (n={r['reference_n_including_errors']})" for r in comparable]
    ax.barh(labels, [r['reference_match_including_errors'] * 100 for r in comparable], color='#2563eb')
    ax.set(xlim=(0, 100), xlabel='Directory-reference agreement including execution failures (%)', title='Source strata differ substantially; groups with n < 10 omitted')
    fig.tight_layout();fig.savefig(figures / 'source_reference_rates.png', dpi=160);plt.close(fig)
    controls = summary['paired_comparisons']
    keys = list(controls)
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.barh(keys, [100 * (controls[k]['paired_variant_reference_rate'] - controls[k]['paired_baseline_reference_rate']) for k in keys], color='#e59632')
    ax.axvline(0, color='#334155', linewidth=1)
    ax.set(xlabel='Paired weak-reference change vs original initial agent (percentage points)', title='Controlled perturbations: small, balanced cohort; exploratory results')
    fig.tight_layout();fig.savefig(figures / 'paired_controls.png', dpi=160);plt.close(fig)
    overview = table(['Measure / 指标', 'Value / 数值'], [
        ['Dataset models / 数据集模型数', full['terminal']], ['Successful outputs / 成功输出', len(done)],
        ['Execution failures / 执行失败', full['errors']],
        ['Output coverage / 输出覆盖率', pct(metrics['successful_coverage'])],
        ['Successful-only reference agreement / 成功样本参考匹配', f"{full['reference_matches']}/{len(evaluable)} = {pct(paired['paired_multi_reference_match_rate'])}"],
        ['Reference agreement including failures / 包含执行失败', f"{full['reference_matches']}/{full['reference_evaluable_terminal']} = {pct(full['weak_reference_match_rate'])}"],
        ['Source macro reference agreement / 来源宏平均', pct(full['source_macro_reference_match_rate'])],
        ['Single → multi, paired / 同批单→多 Agent', f"{pct(paired['weak_reference_match_rate'])} → {pct(paired['paired_multi_reference_match_rate'])}"],
        ['Accepted / review / unknown', f"{statuses['accepted']} / {statuses['review_required']} / {statuses['unknown']}"],
        ['Accepted-subset reference agreement / 自动接受子集', f"{metrics['auto_accept_reference_matches']}/{metrics['auto_accept_reference_n']} = {pct(full['accepted_reference_match_rate'])}"],
        ['Accepted coverage among outputs / 自动接受覆盖', pct(metrics['auto_accept_coverage_successful'])],
        ['Recorded API attempts / API 尝试次数', len(requests)], ['Human-verified accuracy / 人工准确率', 'Not available / 未测得']])
    source_table = table(['Source', 'Dataset models', 'Output', 'Error', 'Reference n', 'Agreement incl. errors'],
        [[r['source'], r['dataset_models'], r['done'], r['errors'], r['reference_n_including_errors'], pct(r['reference_match_including_errors'])] for r in source_rows])
    control_table = table(['Experiment', 'Paired n', 'Reference n', 'Initial reference', 'Variant reference', 'Class agreement'],
        [[k, v['n_paired'], v['n_reference_evaluable_paired'], pct(v['paired_baseline_reference_rate']), pct(v['paired_variant_reference_rate']), pct(v['primary_function_agreement_with_initial_baseline'])] for k, v in controls.items()])
    error_table = table(['Execution error', 'Models'], sorted(errors.items(), key=lambda x: -x[1]))
    function_table = table(['Function code', '中文', 'Outputs'], [[k, FUNCTIONS[k]['name'], v] for k, v in prediction_counts.most_common()])
    quality_table = table(['Geometry subgroup', 'Reference n', 'Reference agreement', 'Auto-accept coverage'],
        [[k, v['n'], pct(v['weak_reference_match_rate']), pct(v['auto_accept_coverage'])] for k, v in supplement['geometry_quality_subgroups'].items()])
    latency_table = table(['API role', 'Attempts with latency', 'Median seconds', 'p95 seconds'],
        [[k, v['n_attempts_with_latency'], f"{v['median_seconds']:.2f}", f"{v['p95_seconds']:.2f}"] for k, v in supplement['api_latency_by_role'].items()])
    common = '\n\n'.join(['## Frozen measurements / 冻结统计\n\n' + overview,
        '![Overview](figures/release_overview.png)',
        '## Controlled experiments / 配对对照\n\n' + control_table, '![Paired controls](figures/paired_controls.png)',
        '## Source strata / 来源分层\n\n' + source_table, '![Source strata](figures/source_reference_rates.png)',
        '## Predicted functions / 预测功能分布\n\n' + function_table,
        '## Execution failures / 执行失败\n\n' + error_table,
        '## Geometry subgroups / 几何子组\n\n' + quality_table,
        '## API operations / 接口运行\n\n' + latency_table + '\n\n23,232 attempts were recorded: 22,849 transport/content completions and 383 request errors. A successful HTTP/content response can still fail schema validation. Quota pauses contribute waiting time beyond active inference. Actual token totals and monetary cost were not exposed and are not estimated from request counts. / 请求成功与任务成功分开计数，等待额度也计入墙钟时间；未从调用次数编造 token 或货币费用。'])
    en = f'''# CAD Function Atlas — Experimental Report v0.1.0

[中文报告](EXPERIMENT_REPORT.zh-CN.md) · [Machine-readable metrics](metrics.json) · [Methods](../BENCHMARK_TEST_PLAN.md)

**Status: frozen evaluation of a 7,715-model dataset.** Publication generated {metrics['created']}. Original inference ran from October 16 to October 18, 2026 (Asia/Shanghai); this documentation revision includes no new inference.

## Findings

The dataset contains **7,715 industrial CAD models**: 7,360 yielded structured functional predictions and 355 ended in execution failures. Successful-output weak-reference agreement is 50.01%; including execution failures, agreement is 47.65%. Multi-agent review improves the same successful subset from 49.34% to 50.01%, a **0.67 percentage-point** gain. This does not establish a useful improvement in true functional accuracy.

The acceptance gate selects 752 outputs (10.22% of successful outputs). Of 744 with a directory reference, 700 agree and 44 disagree (94.09% agreement). The remaining eight have no usable reference. This is a selected subset with subjective confidence scores, not an independently validated precision guarantee. Most outputs still require review.

Identical-input triple runs agree in only 12/31 groups (38.71%); mean pairwise agreement is 52.69%. These small-cohort results expose substantial output variability. Geometry, orientation, image order, resolution and serialization changes also affect predictions. The system is a research and review workflow, not a validated autonomous sorting system.

## Task and experimental design

Inputs are triangle meshes from 32 existing source directories. The task predicts one of nine broad functional families plus unknown, together with an open-language function statement and visual evidence. The concrete source-directory class is not the requested output. Fixed family codes make aggregation possible; free-text explanations do not make the family taxonomy unbounded.

Trimesh preserves scene transforms, removes invalid/duplicate faces, normalizes center/scale and records geometry in unknown source units. VTK/PyVista renders eight views. Classifier and reviewer receive the same anonymous evidence in separate calls without seeing each other's output. A validator checks schema, cited view names, subjective confidence, context need and geometry quality. Disputed cases receive four additional views and a third adjudication call, but remain review-required or unknown.

All measured agents use the same MiniMax Token Plan VLM backend. The service does not expose its underlying model identifier. Roles are independent requests, not independent model families. Correlated errors and service changes remain possible. Newly added OpenAI, compatible-chat, Claude and Gemini adapters have **offline wire-format tests only**; no cross-vendor accuracy or live credential validation is claimed.

The evaluation comprises 7,715 main model attempts and 820 additional experimental jobs. A balanced cohort of 62 models supports eleven 62-case ablations/perturbations. Repetition and STL/GLB round-trips each use 32 cases. Ten synthetic probes include three invalid geometries. All 820 additional jobs reached terminal states: 817 outputs and three expected input rejections. Paired comparisons exclude the main case lacking a valid initial output, giving 61 or 31 pairs; usable weak references reduce these to 57 or 29 pairs. Always use the denominators in the table.

## What the metrics mean

Directory-derived function sets are weak references. A known prediction matches if its family belongs to the frozen reference set. Generic ring/disc directories have no reliable function reference and are unscored; references are absent from inference prompts. Unknown predictions and execution failures count as nonmatches in the terminal-reference metric. Historical planning records outside the 7,715-model dataset are excluded from these measurements.

The successful-only paired comparison excludes execution failures and compares exactly the same 7,161 referenced outputs. Source macro agreement averages 30 referenced source strata, not nine human-labeled function classes. Source sizes and completion rates are unequal. Paused scheduling and missing outputs are not random; the remaining corpus cannot be assumed to share measured performance. Scores are subjective, not calibrated probabilities. No human gold labels, held-out functional test set, verified OOD benchmark, actual token totals or monetary cost are available.

## Interpretation and practical limits

1. **Small multi-agent gain:** adjudication creates 418 weak-reference gains and 370 losses, for 48 net gains. This is not evidence of independent expert verification; the same backend can reinforce shared visual misconceptions.
2. **Selective acceptance:** the high accepted-subset agreement applies to roughly a tenth of outputs; 44 accepted disagreements still require case review. Raising confidence thresholds alone cannot establish correctness.
3. **Feature dependence:** paired image-only, single-view and geometry-only controls lose agreement relative to the original initial classifier. The geometry-only blank-image control is a diagnostic of this endpoint/prompt setup, not a dedicated geometry classifier baseline.
4. **Serialization sensitivity:** format round-trips flatten a cleaned scene and may alter precision/topology. Low agreement reflects the combined conversion/rendering/inference pipeline, not a proof that file extensions themselves cause errors. Appearance changes also affect caption contrast, a documented confound.
5. **Reliability:** 226 empty-content responses and 111 network errors account for most failed models; 18 reached the schema/attempt limit. These are execution failures, not verified wrong function predictions. Failures are preserved and were not silently retried into favorable results after the stop.
6. **Reference and sampling limits:** easy-to-recognize source groups and generic geometric shapes behave differently. Partial source coverage and weak reference ambiguity prevent production-accuracy claims. The supplementary source-cluster bootstrap is exploratory and does not turn weak labels into ground truth.

## Dataset and reproducibility

The dataset contains **7,715 industrial CAD models**, including all 355 execution-failure cases. Input SHA-256 values of all published models have been verified. Meshes, rendered views and request evidence are linked through the sample inventory. SQLite also preserves supplementary historical planning metadata with explicit dataset membership; those records do not expand the published model count.

This release is sealed. New inference must use a new experiment root with an explicit provider/model/base-URL identity. Download/extract dataset shards, run environment diagnostics, then inspect the SQLite/JSONL records and gallery. Raw response audits, frozen original source snapshots, dependency versions, input hashes and experiment records support traceability. Current adapter improvements must not be mistaken for the exact original implementation used for every historical request.

The release environment was verified on Windows 11 / Python 3.14.7 with geometry and offscreen rendering. Provider tests use mocked HTTP transports; live inference is paused. CI adds Windows/Linux checks, but a configured workflow is not proof of a successful remote run. See [deployment](DEPLOYMENT.md), [provider configuration](PROVIDERS.md) and [dataset documentation](DATASET.md).

## Recommended next experiments

Build a human-reviewed, assembly-aware functional gold set with a held-out split; resolve overlapping connection/positioning/support definitions; evaluate repeated runs and cost per accepted correct case; compare real vision providers under the same rendered inputs, schema and retry budget; assess class-conditional calibration and failure recovery separately. Do not move production CAD files on this release's predictions alone.

{common}
'''
    zh = f'''# CAD Function Atlas — 实验报告 v0.1.0

[English report](EXPERIMENT_REPORT.md) · [统计 JSON](metrics.json) · [原实验计划](../BENCHMARK_TEST_PLAN.md)

**本报告分析包含 7,715 个模型的冻结评测数据集。** 报告生成时间：{metrics['created']}。真实推理发生于 2026 年 10 月 16—18 日（北京时间）；本次文档修订未新增推理。

## 主要结论

数据集包含 **7,715 个工业 CAD 模型**：7,360 个得到结构化功能结果，355 个执行失败。成功输出的弱参考匹配率为 **50.01%**；将执行失败计入后为 **47.65%**。同批成功可评估样本中，多 Agent 将匹配率从 49.34% 提高到 50.01%，净增 **0.67 个百分点**，尚不能证明真实功能准确率获得有实用价值的改善。

自动接受 752 件，占成功输出 10.22%；其中 744 件有可用目录参考，700 件匹配、44 件不匹配，参考匹配率 **94.09%**。另 8 件无可用参考。这个数字描述经过筛选的子集，不能作为经过人工验证的精度保证。6,008 件需要复核，600 件保留未知。

同输入三次独立调用只有 12/31 组完全一致（38.71%），平均两两一致率 52.69%。小规模重复试验已暴露明显输出波动，方向、视图顺序、分辨率和转换格式也会改变结果。因此当前成果适合研究、候选分类与人工复核，不适合无复核地移动整个工业模型库。

## 任务定义与方法

输入来自 32 个现有零件目录，输出为九个宽泛功能族加 unknown，以及开放自然语言功能描述和视角证据。目录中的具体零件名不是最终任务标签。功能代码是固定上位体系，不能把自由文本解释等同于无限类别识别。

Trimesh 保留场景变换，清理无效与重复面，中心化和尺度归一化；尺寸保留未知源单位，不编造毫米。PyVista/VTK 生成八视图。分类者与复核者分别请求同一服务，互不查看对方结论；校验器检查 JSON、证据引用、主观分数、上下文需求和几何质量。存在争议时增加四视图并调用裁决者；裁决后的争议结果仍待复核或未知，不因多数意见强行接受。

所有真实实验均使用 MiniMax Token Plan VLM，接口没有暴露底层模型版本。三个角色是同一后端上的独立调用，不是三个独立模型，可能产生相关错误。新增的 OpenAI Responses、Chat Completions、兼容接口、Claude、Gemini 支持只完成协议模拟测试，**没有声称测得这些服务的真实效果**。

本次评测包括 7,715 个模型的主测试，以及 820 项附加实验。62 件平衡队列覆盖十一项消融与扰动；32 件队列分别进行两轮重复和 STL/GLB 格式转换；10 件合成探针包含 3 个无效几何。附加实验全部结束：817 项得到输出，3 项按预期被预处理拒绝。因一个主测试没有有效初判，配对数量为 61 或 31；再去掉无可靠参考目录，参考配对为 57 或 29。具体分母见表，不把不同样本直接横向当作精度提升。

## 指标口径

参考来自目录到功能集合的冻结映射，属于弱参考。已知预测属于允许的参考集合时记为匹配；ring、disc 等仅表示通用外形的目录不强制赋予功能参考，不参与匹配率。目录、文件名与参考映射不传入模型提示词。

包含执行失败的指标把 unknown 和错误任务记为不匹配；数据集范围以 7,715 个模型清单为准，范围外的历史计划行不参与指标计算。成功样本单/多 Agent 比较使用同一批 7,161 个有参考输出。来源宏平均是 30 个有参考原目录的平均，不是九类人工功能真值的宏平均。原目录严重不均衡，各目录完成比例不同，停止与缺失并非随机，不能外推剩余样本性能。置信分数未经校准。没有人工真值、独立留出功能测试集、真实域外拒识基准，也没有可验证的 token 总量或货币成本。

## 误差与有效性分析

1. **多 Agent 收益有限。** 冻结统计显示 418 次参考增益、370 次参考损失，净增 48 件。共同后端可能强化相同视觉误解，角色独立并不意味着判断独立或已经验证正确。
2. **自动接受覆盖低。** 94.09% 只适用于约十分之一输出；仍有 44 个高置信接受结果与参考冲突，需要人工检查参考和模型双方，不能直接称为 44 个已确认错误。
3. **输入依赖明显。** 图像、几何、视角之间存在互补。仅几何的空白图试验反映此视觉端点及提示设置的表现，不是专门几何分类器的性能基线。
4. **转换有混杂因素。** STL/GLB 重新导出会扁平化清理后的场景，并可能改变数值精度或拓扑；格式一致性结果衡量转换—渲染—推理整条链，不能断言扩展名本身导致错误。颜色/背景试验还改变文字对比度，不能只归因于表面颜色。
5. **接口可靠性需单独改进。** 355 个失败中，226 个空响应、111 个网络异常、18 个达到格式/尝试上限。执行失败与功能误判分开；停止后没有补跑以筛选更有利的结果。
6. **弱参考与抽样限制。** 来源类别之间差异很大；难区分的通用形状需要装配关系和用途信息。补充报告中的来源簇 bootstrap 只是探索性区间，不能把弱标签变成真实准确率或因果结论。

## 数据集与复现

数据集包含 **7,715 个工业 CAD 模型**，包括全部 355 个执行失败样本。已核对所有发布模型的 SHA-256；网格、渲染图与请求证据通过样本清单关联。SQLite 同时保留带有数据集成员标识的历史计划元数据；这些附加记录不计入数据集模型数量。

当前发布已封存；新模型/供应商实验应创建新输出目录。数据包包含网格、渲染图、SQLite、JSONL、清单、请求审计和原执行源码快照，足以追溯输入和历史请求。当前新增适配器及修复不能被当作每条历史请求实际执行的版本。部署检查见 [DEPLOYMENT](DEPLOYMENT.md)，接口配置见 [PROVIDERS](PROVIDERS.md)，数据结构见 [DATASET](DATASET.md)。

本机已实测 Windows 11 / Python 3.14.7 的依赖、几何与离屏渲染。供应商协议测试使用模拟 HTTP，不消费新 API 额度。CI 配置包含 Windows/Linux；配置存在不等于远端工作流已成功。

## 下一步建议

建立带装配语境、双人复核和独立留出集的功能真值；明确连接、定位、支承等重叠边界；固定输入比较不同真实视觉服务；重复运行并统计每个正确接受样本的成本；分别验证分数校准、未知判断和网络恢复。当前结论不支持无人复核的生产库重排。

{common}
'''
    # Keep adjudication counts data-driven if a future frozen snapshot differs.
    losses = summary['multi_agent_changes'].get('reference_loss', 0)
    gains = summary['multi_agent_changes'].get('reference_gain', 0)
    en = en.replace('418 weak-reference gains and 370 losses', f'{gains} weak-reference gains and {losses} losses')
    zh = zh.replace('418 次参考增益、370 次参考损失', f'{gains} 次参考增益、{losses} 次参考损失')
    (docs / 'EXPERIMENT_REPORT.md').write_text(en, encoding='utf-8')
    (docs / 'EXPERIMENT_REPORT.zh-CN.md').write_text(zh, encoding='utf-8')
    return metrics


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('outputs/full_benchmark_v1'))
    a = p.parse_args()
    print(json.dumps(build(a.root), ensure_ascii=True, indent=2))
