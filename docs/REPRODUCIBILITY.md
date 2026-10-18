# Reproducibility / 复现说明

## What can be reproduced

The release contains input SHA-256 values, retained original meshes, view assets, geometry measurements, frozen prompts/ontology/references, raw API responses, request timing, SQLite state, experiment configuration and historical executing source snapshots. Metrics and plots can be recalculated without making API requests. The public SQLite paths are portable and the archives retain the relative asset structure.

Exact future API outputs cannot be reproduced deterministically: MiniMax did not expose the underlying model version or sampling seed, the benchmark spans quota windows, and the identical-input repeat experiment shows substantial variation. New protocol adapters and recovery improvements are post-freeze code and do not imply their use for every historical request.

## Measurement boundaries

| Boundary | Frozen definition |
|---|---|
| Original input plan | 10,107 models, no exact duplicate SHA groups |
| Publication subset | 7,715 attempted originals; failures retained |
| Deleted subset | 2,392 never-attempted originals + exclusive artifacts |
| Main outputs | 7,360 |
| Main failures | 355; not human-labeled classification errors |
| Reference scoring | Frozen source-to-function sets, no reference for generic ring/disc |
| Comparison baseline | Initial classifier from exactly the same main cases |
| Controlled comparisons | Variants vs initial classifier, not vs adjudicated final answer |
| Confidence | Subjective model scores, not calibrated probability |
| Human truth | None supplied; blank review rows are not labels |

## Recalculate reports

Download/extract the dataset first. Use its `benchmark` directory as `--root`, preserving the seal. The supplementary diagnostic has a pre-pruning integrity record; a new full-original integrity pass will correctly report the 2,392 intentionally absent inputs. Use membership to verify retained models, not the old plan as an expectation that removed files should exist.

```bash
python -m pytest -q
python cad_doctor.py --render
python cad_benchmark.py status --root downloaded_dataset/benchmark
python cad_benchmark.py report --root downloaded_dataset/benchmark
```

`build_release_report.py` generates the bilingual repository publication report from the local frozen root and requires `supplementary_analysis.json` in that root. The published reports/metadata retain that input. Regeneration timestamps differ from historical inference timestamps; they must not be treated as new inference.

## Tests

Offline tests cover scene transforms, finite geometry and volume gates, source preservation, schema/evidence validation, weak-reference evaluation boundaries, bounded persistent request budgets, quota status 2056, audit-commit recovery, blank-human-label rejection and provider wire formats. Mocked HTTP tests assert request endpoints/image payloads, response parsing, budget limits, no silent provider fallback, safe endpoint configuration and secret/base64 exclusion from audits.

The local environment diagnostic renders a generated cube; it is an environment smoke test, not a functional accuracy benchmark. `--live` explicitly spends one request on the image-only capability probe and is never used by ordinary CI. Remote CI results should be checked on GitHub rather than inferred from workflow presence.

## Compare providers fairly

Use identical retained inputs, rendered views, ontology, prompt roles, schema rules, acceptance threshold and attempt ceilings; create a separate root for every provider/model/base URL. Record model identifiers and raw usage when exposed. Report all failures, unknowns, reference denominators and accepted coverage. Build a human-reviewed held-out set before claiming true accuracy, generalization or superiority. New calls may consume paid quota and are not part of this frozen publication.

中文：本发布提供可追溯的输入、渲染、响应和程序版本，但无法保证未来云 API 逐字重现。弱标签、非随机停止、同后端相关误差与未知模型版本是明确限制；可复算统计不等于实验已具有真实准确率证明。
