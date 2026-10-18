# Changelog

## v0.1.0 — 2026-10-18

- Freeze the explicitly paused MiniMax benchmark: 7,360 outputs, 355 execution failures, 2,392 never-attempted cases from an original 10,107-model plan.
- Retain 7,715 attempted meshes and remove only never-attempted originals and exclusive artifacts, with hashes, membership and cleanup receipts.
- Publish bilingual experimental analysis, machine-readable metrics and a separately hosted Hugging Face dataset.
- Add OpenAI Responses, OpenAI Chat Completions, compatible-chat, Anthropic Messages and Gemini image-input adapters; retain MiniMax as default.
- Add provider/model/base-URL identity checks for new benchmarks, offline environment diagnostics, headless Docker instructions and Windows/Linux CI.
- Recognize Token Plan status 2056, preserve quota refusals separately from role retries, and add bounded transient-response deferral for future runs.
- Historical results remain unchanged. New adapters and retry improvements are not retroactive experimental evidence.

### Known limitations

No human gold labels or true functional accuracy; same-backend agents have correlated errors; repeatability is poor on a small controlled cohort. Non-MiniMax adapters are tested with mocked HTTP only. Historical failed cases remain failed in the released snapshot. Mesh provenance/licensing was not captured by the original input inventory; the code license does not relicense the data.
