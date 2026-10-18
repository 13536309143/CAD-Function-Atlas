# Deployment and environment validation / 部署与环境测试

[English README](../README.md) · [中文 README](../README.zh-CN.md)

## Supported paths and evidence

The release was actually verified on Windows 11, Python 3.14.7, Trimesh 4.12.2, PyVista 0.49.1, VTK 9.7.1 and httpx 0.28.1. `docs/environment.json` records exact local diagnostics. `requirements.lock.txt` preserves the original benchmark environment; `requirements.txt` describes allowed current dependencies. Compatible VTK wheels determine usable Python/platform combinations.

Linux file locking uses `fcntl`; Windows uses `msvcrt`. Child-process window suppression is applied only when available. CI exercises Windows/Linux on Python 3.12. An actual green workflow is required before claiming remote validation; Docker instructions alone are not proof that an image was built or tested.

本机实测环境与 CI 目标环境分开记录。新电脑先运行离线诊断，不需要先配置 API。平台不兼容时不要把依赖导入成功当作 VTK 已能渲染。

## Windows PowerShell

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe cad_doctor.py --render --out outputs/environment_check
if (!(Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

Configure a single vision provider in `.env`, then explicitly test connectivity if desired:

```powershell
# Consumes at most ONE API request; optional and never part of ordinary offline CI
.\.venv\Scripts\python.exe cad_doctor.py --live --out outputs/live_capability_check
```

`--live` sends a generated image-only random-code probe. It can fail because of quota, key permissions, a text-only model, unreadable images or model output. The audit distinguishes these cases. Do not claim a provider is live-verified from mocked tests.

## Linux, headless rendering

Example for Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libegl1 libgl1 libglib2.0-0 libxrender1 libsm6 fonts-dejavu-core
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export PYVISTA_OFF_SCREEN=true
export VTK_DEFAULT_OPENGL_WINDOW=vtkEGLRenderWindow
.venv/bin/python -m pytest -q
.venv/bin/python cad_doctor.py --render
```

The graphics backend depends on the installed VTK build and available Mesa/EGL libraries. If EGL is unavailable, use a compatible X/GLX environment or Xvfb and rerun the rendering diagnostic. No GPU neural model is required; graphics rendering still needs a functioning backend.

## Docker

```bash
docker build -t cad-function-atlas:0.1.0 .
docker run --rm --entrypoint python cad-function-atlas:0.1.0 cad_doctor.py --render
docker run --rm -v "$PWD/data:/app/data:ro" -v "$PWD/outputs:/app/outputs" cad-function-atlas:0.1.0 init --data data --root outputs/docker_new
docker run --rm --env-file .env -v "$PWD/data:/app/data:ro" -v "$PWD/outputs:/app/outputs" cad-function-atlas:0.1.0 run --root outputs/docker_new
```

The Dockerfile copies only source files and requirements; `.env`, data, outputs and credentials are excluded. API secrets are supplied at runtime. A CPU-only machine is sufficient for API inference; rendering may use Mesa software graphics. Disk use depends on corpus size and saved views. The retained v0.1.0 dataset contains several GB of extracted meshes and render artifacts; check archive sizes before downloading.

## CLI lifecycle

1. `cad_benchmark.py init`: freeze input hashes, source sampling, ontology, experiment matrix, request ceiling and provider identity.
2. `prepare`: generate immutable rendering assets in isolated processes; `run` can launch preparation automatically.
3. `run`: persist each request, role output and job state. One visible network request per adapter call; no hidden cross-region or cross-provider fallback.
4. `status`: inspect SQLite counts; `report`: rebuild partial statistics, tables and gallery.
5. `benchmark_postprocess.py`: detailed paired/quality diagnostics and integrity checks, without inference.
6. `evaluate_benchmark_human.py`: score explicitly supplied human labels; blank review rows never become truth.

新实验必须创建新根目录，已有初始化目录不会被覆盖。更换供应商、模型或 base URL 需新建实验，避免混合证据。对 HF 保留子集运行属于新子集评测，不是原 10,107 件全量计划续跑。

## Pause, quota and supervision

Windows foreground or supervised run:

```powershell
.\.venv\Scripts\python.exe benchmark_supervisor.py --root outputs/benchmark_new
powershell -NoProfile -ExecutionPolicy Bypass -File .\Stop-FullBenchmark.ps1 -Root .\outputs\benchmark_new -ListOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\Stop-FullBenchmark.ps1 -Root .\outputs\benchmark_new
```

The supervisor can start/recover a coordinator and has a bounded restart count. The stop command records explicit user intent and stops only processes belonging to the selected benchmark root. It does not install an OS service or boot task. Closing a terminal, rebooting or powering down is not automatic startup configuration.

For Linux, run the coordinator in the foreground and use Ctrl+C, or configure your own process manager. Do not run a restart supervisor if you intend a Ctrl+C to keep the job stopped.

MiniMax interval/weekly quota at or below 1% pauses new work; quotas are polled every 180 seconds and both must exceed 2% before resuming. Rate limiting cools down for 60 seconds. Current code defers transient network/empty responses with a 10-second cooldown while retaining the three non-quota attempts per role and single schema-repair limits. Quota refusals remain in the global request budget. Authentication, unavailable balance and unrecognized resource failures can block and require operator action. Other providers do not expose MiniMax quota percentages and receive no fabricated recovery schedule.

**v0.1.0 is intentionally sealed.** `FROZEN_RELEASE.json` blocks further inference on the pruned original root. The historical 355 failures are preserved; current retry improvements do not retroactively change results.

## Troubleshooting / 排查

| Symptom | Check |
|---|---|
| VTK crash or blank rendering | `cad_doctor.py --render`, graphics libraries, EGL/X backend and available memory |
| API 401/403 | Selected provider key, host/region and model/image permissions |
| API 429 | Audit and cooldown; distinguish rate limiting from quota/balance exhaustion |
| Empty content | Original response and finish/refusal fields; structured output length; avoid counting transport success as inference success |
| Schema failure | Allowed function codes, cited view names, evidence and repair budget |
| Frozen-root error | Expected for v0.1.0; initialize a new root |
| Missing original inputs | Consult release membership; intentionally removed inputs are not corrupt downloads |

Diagnostics: run `python -m pip check`, `python -m pytest -q`, then the offline render check. Logs/SQLite belong in local `outputs/`, not Git. Never paste credentials into an issue.
