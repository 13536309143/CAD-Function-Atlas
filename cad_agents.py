"""Bounded MiniMax Token Plan vision agents; raw responses saved without secrets."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv


class APIError(RuntimeError):
    pass


def parse_json(text: str) -> dict:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()
    decoder = json.JSONDecoder()
    # Accept prose/fenced JSON, but never evaluate generated code.
    for match in re.finditer(r'\{', text):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError('Agent did not return a JSON object')


def validate_prediction(obj: dict, view_names: set[str]):
    required = {'label_zh', 'label_en', 'family', 'confidence', 'unknown',
                'evidence', 'alternatives', 'uncertainties'}
    missing = required - obj.keys()
    if missing:
        raise ValueError(f'Missing prediction fields: {sorted(missing)}')
    for key in ('label_zh', 'label_en', 'family'):
        if not isinstance(obj[key], str) or not obj[key].strip():
            raise ValueError(f'Invalid {key}')
    value = obj['confidence']
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value <= 1:
        raise ValueError('confidence must be numeric within [0, 1]')
    if not isinstance(obj['unknown'], bool):
        raise ValueError('unknown must be boolean')
    for key in ('evidence', 'alternatives', 'uncertainties'):
        if not isinstance(obj[key], list):
            raise ValueError(f'{key} must be a list')
    if not obj['unknown'] and len(obj['evidence']) < 2:
        raise ValueError('Known classifications need at least two visual observations')
    for item in obj['evidence']:
        if not isinstance(item, dict) or item.get('view') not in view_names or not isinstance(item.get('observation'), str) or not item['observation'].strip():
            raise ValueError('Evidence must cite a supplied view and concrete observation')
    if any(not isinstance(item, str) for item in obj['uncertainties']):
        raise ValueError('uncertainties must contain strings')
    for candidate in obj['alternatives']:
        if not isinstance(candidate, dict) or not isinstance(candidate.get('label_en'), str) or not isinstance(candidate.get('reason'), str):
            raise ValueError('Each alternative needs label_en and reason')
    return obj


class MiniMaxVision:
    provider = 'minimax'
    def __init__(self, max_calls=20, timeout=150):
        load_dotenv(Path(__file__).with_name('.env'), override=False)
        self.key = os.getenv('MINIMAX_API_KEY', '').strip()
        self.host = os.getenv('MINIMAX_API_HOST', 'https://api.minimaxi.com').rstrip('/')
        if self.host not in {'https://api.minimaxi.com', 'https://api.minimax.io'}:
            raise ValueError('MINIMAX_API_HOST must be an official MiniMax regional host')
        if not self.key or self.key.startswith('replace_'):
            raise APIError('Configure MINIMAX_API_KEY in .env first')
        self.max_calls = max_calls
        self.calls = 0
        self.timeout = timeout

    def redact(self, value):
        text = str(value).replace(self.key, '[REDACTED]')
        return re.sub(r'sk-[A-Za-z0-9_\-]{16,}', '[REDACTED]', text)

    def call(self, image: Path, prompt: str, audit_path: Path):
        if self.calls >= self.max_calls:
            raise APIError('Configured API call budget exhausted')
        self.calls += 1
        encoded = base64.b64encode(image.read_bytes()).decode('ascii')
        image_url = 'data:image/jpeg;base64,' + encoded
        if image.suffix.lower() == '.png':
            image_url = 'data:image/png;base64,' + encoded
        record = {'endpoint': self.host + '/v1/coding_plan/vlm',
                  'prompt': prompt, 'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                  'call_number': self.calls}
        start = time.monotonic()
        try:
            # One request per call; no hidden retries or cross-region key forwarding.
            with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=True) as client:
                response = client.post(record['endpoint'],
                                       headers={'Authorization': 'Bearer ' + self.key,
                                                'MM-API-Source': 'Minimax-MCP'},
                                       json={'prompt': prompt, 'image_url': image_url})
            record['http_status'] = response.status_code
            record['trace_id'] = response.headers.get('Trace-Id')
            try:
                body = response.json()
            except ValueError:
                raise APIError(f'Non-JSON API response (HTTP {response.status_code})') from None
            record['response'] = body
            if not response.is_success:
                raise APIError(f'HTTP {response.status_code}; check API audit for base_resp')
            base = body.get('base_resp', {})
            if base.get('status_code', 0) != 0:
                raise APIError(f"MiniMax API status {base.get('status_code')}: {base.get('status_msg')}")
            content = body.get('content')
            if not isinstance(content, str) or not content.strip():
                raise APIError('MiniMax returned no image understanding content')
            return content
        except (httpx.HTTPError, APIError) as exc:
            record['error'] = self.redact(exc)
            raise APIError(self.redact(exc)) from None
        finally:
            record['elapsed_seconds'] = round(time.monotonic() - start, 3)
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_text(self.redact(json.dumps(record, ensure_ascii=False, indent=2)), encoding='utf-8')


def create_vision_client(max_calls=20, timeout=150):
    from cad_providers import ProviderVision, provider_identity
    if provider_identity()['provider'] == 'minimax':
        return MiniMaxVision(max_calls, timeout)
    return ProviderVision(max_calls, timeout)


SCHEMA = """仅输出一个JSON对象，不用Markdown。格式：
{"label_zh":"中文具体名称", "label_en":"English singular class name", "family":"机械上位类",
"confidence":0.0, "unknown":false,
"evidence":[{"view":"实际提供的视角名", "observation":"该视图中确实可见的结构"}],
"alternatives":[{"label_en":"候选类别", "reason":"为什么可能/为何排除"}],
"uncertainties":["不能从现有网格确定的内容"]}。
confidence仅为主观分数，不是经校准的概率。证据至少两条且引用图中视角名。
不要虚构可见不到的螺纹、齿数、材料或制造精度。不从文件名猜类别。
不确定时unknown=true，可以输出unknown或宽泛类别。名称不限于预设类别体系。
family给出宽泛机械类别，label_en用最合适的具体零部件名，不输出斜杠拼接类别。"""


def agent_prompt(role: str, geometry: dict, prior=None):
    introductions = {
        'classifier': '你是机械结构识别工程师。根据同一零部件的8个视图与几何测量，独立提出开放词汇分类。先观察结构，再判断类别及可疑相似项。',
        'reviewer': '你是独立机械检验工程师。你没有其他人的分类结论。请独立识别这组8视图，重点检查功能结构，并指出常见相似件之间的区别。证据不足就保留未知。',
        'adjudicator': '你是争议裁决工程师。图片是同一零件新生成的4个补充观察视图。两名独立识别者未满足一致接受条件。结合新证据裁决，也可以推翻两者或保留未知。不要为了达成一致提高置信度。',
    }
    prompt = introductions[role] + '\n' + SCHEMA + '\n几何测量（单位未知）：\n' + json.dumps(geometry, ensure_ascii=False)
    if prior is not None:
        prompt += '\n此前两个独立结果（仅作为待检验的假设）：\n' + json.dumps(prior, ensure_ascii=False)
    return prompt


def infer(client, image, role, geometry, out, view_names, prior=None):
    prompt = agent_prompt(role, geometry, prior)
    prompt += '\n本次图中视角名仅限：' + ', '.join(view_names) + '。evidence.view必须逐字使用此列表中的单个值，不得引用其他视角名。'
    raw = client.call(image, prompt, out / f'{role}_api.json')
    result = validate_prediction(parse_json(raw), set(view_names))
    (out / f'{role}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
