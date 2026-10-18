# Vision API adapters / 多供应商视觉接口

All adapters implement `call(image_path, prompt, audit_path) -> text`. The renderer sends one labeled multi-view montage per role; the same geometry, schema validation and review policy apply. Model IDs are explicit configuration, not silently upgraded defaults. Only MiniMax was used in the frozen benchmark.

## Configuration

| `LLM_PROVIDER` | Protocol | Default API base | Credential |
|---|---|---|---|
| `minimax` (default) | Token Plan VLM | `https://api.minimaxi.com` | `MINIMAX_API_KEY` |
| `openai_responses` / `openai` | Responses | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `openai_chat` | Chat Completions | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `openai_compatible` | Compatible Chat Completions | Must set `LLM_BASE_URL` | `LLM_API_KEY` |
| `anthropic` / `claude` | Messages | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| `gemini` | GenerateContent | `https://generativelanguage.googleapis.com/v1beta` | `GEMINI_API_KEY` |

Non-MiniMax providers require `LLM_MODEL`. `LLM_API_KEY`, when nonempty, overrides the selected vendor-specific key. `LLM_MAX_OUTPUT_TOKENS` defaults to 2048. Set `LLM_BASE_URL` only to an endpoint you intend to send the selected key and CAD images to. No redirects or host fallbacks are followed. HTTPS is required except for a local HTTP server (`localhost`, `127.0.0.1`, `::1`).

`.env` is loaded without overriding process environment variables. Remove obsolete `LLM_API_KEY` or base-URL overrides when switching providers. A ChatGPT browser subscription/session is not the API credential consumed by this program.

## Examples

```dotenv
# MiniMax China; international keys must explicitly use https://api.minimax.io
LLM_PROVIDER=minimax
MINIMAX_API_KEY=replace_with_your_token_plan_key
MINIMAX_API_HOST=https://api.minimaxi.com
```

```dotenv
LLM_PROVIDER=openai_responses
LLM_MODEL=replace_with_an_available_vision_model
OPENAI_API_KEY=replace_with_your_key
LLM_MAX_OUTPUT_TOKENS=2048
```

```dotenv
LLM_PROVIDER=anthropic
LLM_MODEL=replace_with_an_available_vision_model
ANTHROPIC_API_KEY=replace_with_your_key
```

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=replace_with_an_available_vision_model
GEMINI_API_KEY=replace_with_your_key
```

```dotenv
# A hosted compatible vision endpoint, or your own local server
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=replace_with_a_supported_vision_model
LLM_API_KEY=replace_with_your_key
```

Compatibility is at the wire-protocol level. Qwen-VL, GLM-V, Doubao, gateways and local servers can be tried only when their selected endpoint/model accepts image-url content blocks. Some vendors require different token parameters, image limits or extra headers; provider naming alone does not guarantee support. The generic adapter uses `max_tokens`, OpenAI chat uses `max_completion_tokens`, Responses uses `max_output_tokens`, Anthropic uses `max_tokens`, and Gemini uses `generationConfig.maxOutputTokens`.

## Evidence and audit policy

| Path | Offline tests | Real measured benchmark |
|---|---|---|
| MiniMax Token Plan | Schema/budget/quota tests | Yes; backend model ID not exposed |
| OpenAI Responses / Chat | Mocked HTTP request and parsing tests | No |
| Compatible Chat | Mocked HTTP request and parsing tests | No vendor-specific certification |
| Claude / Gemini | Mocked HTTP request and parsing tests | No |

Audits record provider/model, endpoint, prompt, image SHA, response and elapsed time. Keys/auth headers and image base64 are not logged. Generic adapters normalize successful response text to the existing schema while retaining the vendor response. Gemini thought parts are excluded from the final text parser. HTTP success with empty text is an execution failure, not a successful classification. The current coordinator can defer transient failures under its persistent attempt budget; there are no hidden SDK retries.

新接口的“支持”指请求格式、响应解析、预算和审计已实现并通过离线测试，不代表账号权限、当前服务可用性或分类效果已验证。暂停后的发布流程不再发送真实推理。需要真实验证时，显式运行 `cad_doctor.py --live`，再新建小规模实验。

## Official protocol references

- [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses)
- [OpenAI Responses create](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Claude vision](https://platform.claude.com/docs/en/build-with-claude/vision)
- [Gemini image understanding](https://ai.google.dev/gemini-api/docs/image-understanding)

The adapters follow these image-input formats; account-level limits and model availability remain provider-specific.
