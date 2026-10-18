# CAD Function Atlas v0.1.0

工业 CAD 零部件功能分类与评测 / Multimodal functional classification and evaluation for industrial CAD meshes.

This first research release freezes an intentionally stopped MiniMax experiment and publishes the software, bilingual analysis and retained evidence.

## Included

- Local OBJ/STL/GLB cleanup, geometry measurements, eight-view rendering and four-view dispute review.
- Structured classifier/reviewer/adjudicator workflow, explicit unknown/review states and human-label evaluation tooling.
- MiniMax plus OpenAI Responses/Chat Completions, compatible-chat, Claude and Gemini adapters. Non-MiniMax adapters have mocked protocol tests only.
- Chinese/English READMEs, deployment/environment checks, Docker recipe, Windows/Linux CI and detailed experiment reports.
- Frozen metrics, source-stratum tables and plots. The release snapshot preserves execution failures and the incomplete original plan.

## Measured snapshot

Original plan: 10,107 models. Successful outputs: 7,360; execution failures: 355; never attempted and removed: 2,392. All 820 controlled/probe jobs reached terminal states, including three expected invalid-geometry rejections.

Successful-output directory-reference agreement: **50.01%**; including execution failures: **47.65%**. Same-case single → multi-agent: **49.34% → 50.01%**. Accepted-subset agreement: **94.09%**, covering **10.22%** of successful outputs. Identical-input three-run unanimity: **38.71%**.

These are weak-reference measurements, **not human-verified functional accuracy**. The release is experimental and does not justify autonomous production sorting.

## Downloads

- `cad-function-atlas-v0.1.0.zip`: application source, tests, bilingual documentation and compact report assets.
- `benchmark-snapshot-v0.1.0.zip`: portable database, tables, membership, environment evidence and reports; large meshes/renders are hosted separately.
- `SHA256SUMS`: integrity hashes for the release assets.
- Full retained data: [GeorgeWJJ/cad-function-atlas](https://huggingface.co/datasets/GeorgeWJJ/cad-function-atlas).

保留全部 7,715 件已尝试模型，包括失败样本；仅清理 2,392 件从未尝试的输入和专属附加文件。旧根目录已封存，后续实验需新建输出目录。没有发布密钥或模型权重；代码 MIT 不重新授权原 CAD 数据。
