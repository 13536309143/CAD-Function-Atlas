# CAD Function Atlas — Experimental Report v0.1.0

[中文报告](EXPERIMENT_REPORT.zh-CN.md) · [Machine-readable metrics](metrics.json) · [Methods](../BENCHMARK_TEST_PLAN.md)

**Status: frozen evaluation of a 7,715-model dataset.** Publication generated 2026-10-18T19:03:16+08:00. Original inference ran from October 16 to October 18, 2026 (Asia/Shanghai); this documentation revision includes no new inference.

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

1. **Small multi-agent gain:** adjudication creates 431 weak-reference gains and 383 losses, for 48 net gains. This is not evidence of independent expert verification; the same backend can reinforce shared visual misconceptions.
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

## Frozen measurements / 冻结统计

| Measure / 指标 | Value / 数值 |
| --- | --- |
| Dataset models / 数据集模型数 | 7715 |
| Successful outputs / 成功输出 | 7360 |
| Execution failures / 执行失败 | 355 |
| Output coverage / 输出覆盖率 | 95.40% |
| Successful-only reference agreement / 成功样本参考匹配 | 3581/7161 = 50.01% |
| Reference agreement including failures / 包含执行失败 | 3581/7516 = 47.65% |
| Source macro reference agreement / 来源宏平均 | 56.67% |
| Single → multi, paired / 同批单→多 Agent | 49.34% → 50.01% |
| Accepted / review / unknown | 752 / 6008 / 600 |
| Accepted-subset reference agreement / 自动接受子集 | 700/744 = 94.09% |
| Accepted coverage among outputs / 自动接受覆盖 | 10.22% |
| Recorded API attempts / API 尝试次数 | 23232 |
| Human-verified accuracy / 人工准确率 | Not available / 未测得 |

![Overview](figures/release_overview.png)

## Controlled experiments / 配对对照

| Experiment | Paired n | Reference n | Initial reference | Variant reference | Class agreement |
| --- | --- | --- | --- | --- | --- |
| appearance | 61 | 57 | 70.18% | 63.16% | 60.66% |
| format_glb | 31 | 29 | 68.97% | 41.38% | 32.26% |
| format_stl | 31 | 29 | 68.97% | 58.62% | 45.16% |
| geometry_only | 61 | 57 | 70.18% | 0.00% | 1.64% |
| image_only | 61 | 57 | 70.18% | 49.12% | 47.54% |
| iso_pair | 61 | 57 | 70.18% | 45.61% | 49.18% |
| low_resolution | 61 | 57 | 70.18% | 56.14% | 55.74% |
| orthographic | 61 | 57 | 70.18% | 61.40% | 47.54% |
| repeat_1 | 31 | 29 | 68.97% | 55.17% | 41.94% |
| repeat_2 | 31 | 29 | 68.97% | 55.17% | 54.84% |
| rotate_a | 61 | 57 | 70.18% | 61.40% | 57.38% |
| rotate_b | 61 | 57 | 70.18% | 59.65% | 62.30% |
| scale_translate | 61 | 57 | 70.18% | 57.89% | 52.46% |
| single_iso | 61 | 57 | 70.18% | 42.11% | 36.07% |
| view_shuffle | 61 | 57 | 70.18% | 64.91% | 68.85% |

![Paired controls](figures/paired_controls.png)

## Source strata / 来源分层

| Source | Dataset models | Output | Error | Reference n | Agreement incl. errors |
| --- | --- | --- | --- | --- | --- |
| bearing | 266 | 262 | 4 | 266 | 72.56% |
| bushing | 142 | 142 | 0 | 142 | 48.59% |
| castors_and_wheels | 72 | 72 | 0 | 72 | 63.89% |
| chain_drive | 19 | 19 | 0 | 19 | 26.32% |
| clamp | 31 | 31 | 0 | 31 | 80.65% |
| collar | 10 | 10 | 0 | 10 | 50.00% |
| disc | 21 | 21 | 0 | 0 | — |
| fitting | 327 | 315 | 12 | 327 | 57.49% |
| flange | 1 | 1 | 0 | 1 | 0.00% |
| gear | 97 | 96 | 1 | 97 | 90.72% |
| handles | 323 | 311 | 12 | 323 | 59.75% |
| hinge | 12 | 12 | 0 | 12 | 66.67% |
| hook | 24 | 24 | 0 | 24 | 62.50% |
| joint | 336 | 322 | 14 | 336 | 33.33% |
| key_and_spline | 852 | 804 | 48 | 852 | 0.82% |
| linear_guide | 10 | 10 | 0 | 10 | 40.00% |
| motor | 148 | 148 | 0 | 148 | 75.00% |
| nut | 424 | 387 | 37 | 424 | 75.00% |
| pin | 1205 | 1147 | 58 | 1205 | 29.29% |
| plate | 60 | 60 | 0 | 60 | 71.67% |
| plug | 34 | 34 | 0 | 34 | 47.06% |
| pulley | 61 | 61 | 0 | 61 | 59.02% |
| ring | 178 | 178 | 0 | 0 | — |
| rivet | 464 | 415 | 49 | 464 | 25.65% |
| rotor | 94 | 94 | 0 | 94 | 92.55% |
| screws_and_bolts | 1204 | 1167 | 37 | 1204 | 88.46% |
| spacer | 22 | 22 | 0 | 22 | 59.09% |
| spring | 69 | 69 | 0 | 69 | 89.86% |
| stud | 636 | 598 | 38 | 636 | 13.05% |
| switch | 36 | 36 | 0 | 36 | 66.67% |
| valve | 1 | 1 | 0 | 1 | 100.00% |
| washer | 536 | 491 | 45 | 536 | 54.48% |

![Source strata](figures/source_reference_rates.png)

## Predicted functions / 预测功能分布

| Function code | 中文 | Outputs |
| --- | --- | --- |
| connection_fastening | 连接与紧固 | 2498 |
| support_guidance | 支承与导向 | 1359 |
| positioning_spacing | 定位限位与间隔 | 1359 |
| unknown | 待确认功能 | 600 |
| transmission_drive | 动力与传动 | 405 |
| structural_support | 结构承载与防护 | 300 |
| fluid_control | 流体输送与控制 | 292 |
| operation_control | 操作与控制 | 249 |
| sealing_closure | 密封与封堵 | 221 |
| elastic_buffering | 弹性储能与缓冲 | 77 |

## Execution failures / 执行失败

| Execution error | Models |
| --- | --- |
| MiniMax returned no image understanding content | 226 |
| [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1082) | 93 |
| Per-role attempt/JSON repair limit reached | 18 |
| [WinError 10054] 远程主机强迫关闭了一个现有的连接。 | 10 |
| Server disconnected without sending a response. | 5 |
| The read operation timed out | 1 |
| [WinError 10051] 向一个无法连接的网络尝试了一个套接字操作。 | 1 |
| [Errno 11001] getaddrinfo failed | 1 |

## Geometry subgroups / 几何子组

| Geometry subgroup | Reference n | Reference agreement | Auto-accept coverage |
| --- | --- | --- | --- |
| not_watertight | 2752 | 66.02% | 15.23% |
| volume_unavailable | 2753 | 66.04% | 15.22% |
| triangles_10k_to_100k | 493 | 76.47% | 33.27% |
| watertight | 4409 | 40.01% | 7.37% |
| volume_valid | 4408 | 40.00% | 7.37% |
| triangles_1k_to_10k | 3018 | 64.38% | 14.08% |
| triangles_lt_1k | 3598 | 33.74% | 3.95% |
| triangles_ge_100k | 52 | 90.38% | 25.00% |

## API operations / 接口运行

| API role | Attempts with latency | Median seconds | p95 seconds |
| --- | --- | --- | --- |
| reviewer | 7697 | 7.08 | 10.91 |
| classifier | 8786 | 7.73 | 12.04 |
| probe | 1 | 3.95 | 3.95 |
| adjudicator | 6748 | 7.04 | 9.67 |

23,232 attempts were recorded: 22,849 transport/content completions and 383 request errors. A successful HTTP/content response can still fail schema validation. Quota pauses contribute waiting time beyond active inference. Actual token totals and monetary cost were not exposed and are not estimated from request counts. / 请求成功与任务成功分开计数，等待额度也计入墙钟时间；未从调用次数编造 token 或货币费用。
