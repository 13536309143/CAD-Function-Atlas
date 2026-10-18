---
language:
- en
- zh
pretty_name: CAD Function Atlas
size_categories:
- 1K<n<10K
tags:
- cad
- 3d
- industrial-parts
- multimodal
- functional-classification
- weak-supervision
- evaluation
configs:
- config_name: samples
  data_files:
  - split: retained
    path: records/samples.jsonl
- config_name: jobs
  data_files:
  - split: historical_plan
    path: records/jobs.jsonl
- config_name: membership
  data_files:
  - split: original_plan
    path: records/release_membership.jsonl
---

# CAD Function Atlas — v0.1.0

**Industrial CAD functional classification: retained meshes, multi-view evidence and a frozen partial API benchmark.**

[Code and bilingual READMEs](https://github.com/13536309143/llm) · [English analysis](https://github.com/13536309143/llm/blob/main/docs/EXPERIMENT_REPORT.md) · [中文分析](https://github.com/13536309143/llm/blob/main/docs/EXPERIMENT_REPORT.zh-CN.md) · [Download/schema](https://github.com/13536309143/llm/blob/main/docs/DATASET.md)

## Status and scope

The owner intentionally stopped an original 10,107-model / 32-directory benchmark and removed 2,392 models never sent for inference, together with their exclusive artifacts. **All 7,715 attempted originals are retained**, including 355 execution failures. The release has 7,360 successful main outputs, not 10,107 completed classifications. Historical planned rows and explicit retention membership preserve honest completion accounting. No cases were discarded for an unfavorable classification outcome.

The task predicts nine broad function families plus unknown, with natural-language statements and cited views. All measured inference uses MiniMax Token Plan VLM; the backend model version is not exposed. Other provider adapters in the code repository have offline protocol tests only.

## Measured results

| Measure | Result |
|---|---:|
| Successful outputs / original plan | 7,360 / 10,107 = 72.82% |
| Successful-output weak-reference agreement | 3,581 / 7,161 = 50.01% |
| Agreement including execution failures | 3,581 / 7,516 = 47.65% |
| Single → multi-agent, same cases | 49.34% → 50.01% |
| Auto-accepted outputs | 752 (10.22% of successful outputs) |
| Accepted-subset weak-reference agreement | 700 / 744 = 94.09% |
| Identical-input three-run unanimity | 12 / 31 = 38.71% |
| Additional controlled jobs | 820 ended: 817 outputs, 3 expected invalid-input rejections |

**These are directory-derived weak-reference agreements, not human-verified functional accuracy.** There are no human gold labels. The selected accepted subset contains 44 disagreements and eight unscored cases. Unknowns and execution failures are preserved. Same-backend agent errors can correlate. Partial completion is not random and cannot establish full-corpus or production accuracy.

## Contents

- `records/samples.jsonl`: 7,715 retained attempted originals with portable paths, SHA, source, reference sets and main outcome.
- `records/jobs.jsonl`: all 10,927 planned main/control/synthetic tasks, serialized payloads, explicit retained/pruned membership.
- `records/requests.jsonl`: 23,232 recorded request attempts and audit pointers.
- `records/release_membership.jsonl`: original 10,107 inputs, retention and reason.
- `benchmark/benchmark.sqlite`: portable historical database, including membership and the frozen release seal.
- `meshes/*.zip`: retained raw meshes, extracting to `data/`.
- `renders/*.zip`: retained base/control/synthetic render artifacts, extracting to `benchmark/assets/`.
- `audit/*.zip`: request responses, role outputs, historical source/configuration, cleanup provenance and synthetic probes.
- `benchmark/*.html`, CSV and JSON: browsable evidence and statistical exports after render extraction.
- `reports/`: bilingual report, figures, metrics and verified environment record.
- `SHA256SUMS`: published-file integrity hashes.

Example:

```python
from huggingface_hub import snapshot_download
snapshot_download("GeorgeWJJ/cad-function-atlas", repo_type="dataset", local_dir="cad_function_atlas")
```

The code repository provides `download_dataset.py --extract` for checksum validation and path-safe extraction. Open the gallery only after extracting render shards. The snapshot is sealed: initialize a new benchmark root for new inference. Removed meshes are intentionally absent, not failed downloads.

## Provenance and licensing

The initial CAD collection was supplied locally by the repository owner. Upstream per-model source URLs, authors and license grants were not recorded. This dataset does not invent or grant an upstream mesh license. The repository's MIT license applies to source code and documentation, **not** to third-party CAD meshes or derived assets. Establish applicable original rights before redistributing or using those assets. No neural model weights are included.

Public copies replace machine-specific workspace paths with relative paths. API keys, authentication headers and encoded image request bodies are excluded from audits. Prompts and generated responses remain available for experimental traceability. Original SHA values were verified before deletion, and all retained files are indexed.

## 中文说明

这是主动停止后的阶段数据发布，原全量计划未完成。保留全部 7,715 件已尝试模型，包括失败样本；删除 2,392 件未调用推理的模型及专属附加文件，不按预测好坏筛选。发布包含网格、渲染、SQLite、JSONL、原始响应、执行源码快照和清理凭据。

目录映射属于弱参考，没有人工功能真值，不能把参考匹配率称作真实准确率。所有真实实验使用同一 MiniMax 后端，多角色可能相关出错；新增其他 API 的代码支持不代表已做跨供应商真实评测。原网格逐件来源与许可缺失，代码 MIT 不扩展到网格内容。
