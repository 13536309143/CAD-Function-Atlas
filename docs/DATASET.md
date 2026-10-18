# Dataset / 数据集

**Repository:** [GeorgeWJJ/cad-function-atlas](https://huggingface.co/datasets/GeorgeWJJ/cad-function-atlas) · **Version:** v0.1.0 · **Status:** frozen partial experiment.

## Scope

The original plan lists 10,107 industrial CAD meshes across 32 source folders. The released geometry subset contains **7,715 attempted originals**, including **7,360 successful outputs and 355 execution failures**. Exactly **2,392 never-attempted originals and their exclusive artifacts were removed**, following the owner's instruction; historical rows remain to make the incomplete coverage visible. No cases were removed because their prediction disagreed with a weak reference.

The directory function sets are weak references, not human labels. Nine function families plus unknown are predictions. No verified ground-truth functional accuracy, train/test split or real OOD rejection benchmark is provided. The release is an observational evaluation snapshot, not a fully labeled supervised training dataset.

## Files

| Path | Contents |
|---|---|
| `README.md` | Bilingual dataset card and measured limitations |
| `records/samples.jsonl` | Retained attempted inputs, source, relative path, input SHA, reference set and main outcome |
| `records/jobs.jsonl` | All original planned experiment rows, explicit retained/pruned membership, serialized prediction payload |
| `records/requests.jsonl` | Request attempts, roles, states, timing and audit references |
| `records/release_membership.jsonl` | All 10,107 original inputs and retention reasons |
| `benchmark/benchmark.sqlite` | Portable SQLite with historical plan and membership; `samples.path` is relative |
| `benchmark/FROZEN_RELEASE.json` | Seal and original-plan counts; blocks inference continuation |
| `benchmark/gallery.html` | Successful/error main cases, referencing extracted render assets |
| `benchmark/controlled_gallery.html` | Final refreshed controlled-comparison gallery |
| `meshes/meshes-*.zip` | Retained original files; extract from dataset root into `data/` |
| `renders/renders-*.zip` | Retained base/control/synthetic views; extract into `benchmark/assets/` |
| `audit/requests.zip` | Per-request original response audits, without credentials/image base64 |
| `audit/provenance.zip` | Historical execution source/config/manifest, cleanup evidence and job-role outputs |
| `reports/` | Reports, charts, compact metrics and environment evidence |
| `SHA256SUMS` | Integrity hashes of every published artifact except the checksum file itself |

Shards use ZIP64 with bounded approximate sizes and ordinary ZIP extraction. There are no neural weights or credentials. The SQLite schema preserves original tables (`samples`, `assets`, `jobs`, `requests`, `state`, `events`) plus `release_membership`. A planned row with `retained=false` is evidence of incomplete coverage; its missing mesh is intentional.

## Download

```bash
python -m pip install -r requirements-publish.txt
python download_dataset.py --out downloaded_dataset
# Optional: also extract the mesh/render/audit archives, after checking free disk space
python download_dataset.py --out downloaded_dataset --extract
```

To download metadata only:

```python
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="GeorgeWJJ/cad-function-atlas", repo_type="dataset",
    local_dir="downloaded_dataset", allow_patterns=["README.md", "records/*", "benchmark/*.sqlite", "benchmark/FROZEN_RELEASE.json", "reports/*", "SHA256SUMS"]
)
```

Extract full data before opening the gallery. Serve the dataset root locally (`python -m http.server 8000 --directory downloaded_dataset`) and open `/benchmark/gallery.html`. SQLite records can be read directly without mesh extraction. For new inference, initialize a **new** benchmark root using extracted `data/`; do not resume the sealed snapshot or claim the retained subset is the original full corpus.

## Privacy, provenance and rights

Public records replace machine-specific absolute workspace paths with portable relative paths. Requests contain generated analysis and prompts rather than API keys, auth headers or encoded images. All original input hashes were checked before pruning; retention membership and deletion receipts are preserved.

The original collection was supplied locally by the repository owner. Per-mesh upstream source URLs, authors and license grants were not present in the initial inventory. This release does **not** infer or grant an upstream mesh license; the MIT code license covers code/documentation only. Reuse of mesh/derived assets requires establishing applicable original rights. The card records this provenance gap instead of inventing a permissive data license.

中文说明：数据集公开发布；保留全部已尝试模型，包括错误样本，未按预测好坏筛选。模型原目录只作为弱参考。原网格来源与逐件许可缺失，代码 MIT 许可不扩展到网格。数据库保留未执行计划行，便于核对清理范围与未完成比例。
