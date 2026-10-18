"""Vision HTTP adapters. No SDK, hidden retries, endpoint fallback or image logging."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv


DEFAULTS = {
    'openai_responses': ('https://api.openai.com/v1', 'OPENAI_API_KEY'),
    'openai_chat': ('https://api.openai.com/v1', 'OPENAI_API_KEY'),
    'openai_compatible': ('', 'LLM_API_KEY'),
    'anthropic': ('https://api.anthropic.com/v1', 'ANTHROPIC_API_KEY'),
    'gemini': ('https://generativelanguage.googleapis.com/v1beta', 'GEMINI_API_KEY'),
}


def provider_identity():
    load_dotenv(Path(__file__).with_name('.env'), override=False)
    provider = os.getenv('LLM_PROVIDER', 'minimax').strip().lower()
    provider = {'openai': 'openai_responses', 'claude': 'anthropic'}.get(provider, provider)
    if provider != 'minimax' and provider not in DEFAULTS:
        raise ValueError('Unsupported LLM_PROVIDER: ' + provider)
    default = DEFAULTS.get(provider, ('', ''))[0]
    base = os.getenv('LLM_BASE_URL', default).rstrip('/') if provider != 'minimax' else os.getenv('MINIMAX_API_HOST', 'https://api.minimaxi.com').rstrip('/')
    return {'provider': provider, 'model': os.getenv('LLM_MODEL', '').strip() if provider != 'minimax' else 'provider-managed', 'base_url': base}


class ProviderVision:
    def __init__(self, max_calls=20, timeout=150):
        from cad_agents import APIError
        identity = provider_identity()
        self.provider, self.model, self.host = identity['provider'], identity['model'], identity['base_url']
        if self.provider not in DEFAULTS:
            raise ValueError('ProviderVision requires a non-MiniMax provider')
        self.key = os.getenv('LLM_API_KEY', '').strip() or os.getenv(DEFAULTS[self.provider][1], '').strip()
        if not self.key or self.key.startswith('replace_'):
            raise APIError('Configure ' + DEFAULTS[self.provider][1] + ' or LLM_API_KEY')
        if not self.model:
            raise ValueError('Set LLM_MODEL to a vision-capable model available to your account')
        parsed = urlparse(self.host)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise ValueError('LLM_BASE_URL must not contain credentials, query parameters or fragments')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('Use HTTPS, or HTTP only for a local inference server')
        self.max_calls, self.calls, self.timeout = max_calls, 0, timeout
        self.max_output = int(os.getenv('LLM_MAX_OUTPUT_TOKENS', '2048'))
        if self.max_output <= 0:
            raise ValueError('LLM_MAX_OUTPUT_TOKENS must be positive')

    def redact(self, value):
        text = str(value)
        for name, secret in os.environ.items():
            if secret and len(secret) >= 8 and any(word in name.upper() for word in ('API_KEY', 'AUTH_TOKEN', 'HF_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN')):
                text = text.replace(secret, '[REDACTED]')
        text = text.replace(self.key, '[REDACTED]')
        return re.sub(r'(?:sk-|hf_|ghp_|github_pat_)[A-Za-z0-9_\-]{16,}', '[REDACTED]', text)

    def request_spec(self, encoded, mime, prompt):
        data_url = f'data:{mime};base64,{encoded}'
        headers = {'Authorization': 'Bearer ' + self.key}
        if self.provider == 'openai_responses':
            return self.host + '/responses', headers, {'model': self.model, 'store': False,
                'max_output_tokens': self.max_output, 'input': [{'role': 'user', 'content': [
                    {'type': 'input_text', 'text': prompt}, {'type': 'input_image', 'image_url': data_url}]}]}
        if self.provider in {'openai_chat', 'openai_compatible'}:
            token_field = 'max_completion_tokens' if self.provider == 'openai_chat' else 'max_tokens'
            return self.host + '/chat/completions', headers, {'model': self.model, token_field: self.max_output,
                'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': data_url}}]}]}
        if self.provider == 'anthropic':
            return self.host + '/messages', {'x-api-key': self.key, 'anthropic-version': '2023-06-01'}, {
                'model': self.model, 'max_tokens': self.max_output, 'messages': [{'role': 'user', 'content': [
                    {'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': encoded}},
                    {'type': 'text', 'text': prompt}]}]}
        if not re.fullmatch(r'[A-Za-z0-9._-]+', self.model):
            raise ValueError('Gemini LLM_MODEL must be a model identifier, without URL paths')
        return self.host + '/models/' + self.model + ':generateContent', {'x-goog-api-key': self.key}, {
            'contents': [{'role': 'user', 'parts': [{'text': prompt}, {'inline_data': {'mime_type': mime, 'data': encoded}}]}],
            'generationConfig': {'maxOutputTokens': self.max_output}}

    def extract_text(self, body):
        if self.provider == 'openai_responses':
            return '\n'.join(part.get('text', '') for item in body.get('output', []) if item.get('type') == 'message'
                             for part in item.get('content', []) if part.get('type') == 'output_text')
        if self.provider in {'openai_chat', 'openai_compatible'}:
            choices = body.get('choices', [])
            content = choices[0].get('message', {}).get('content') if choices else None
            if isinstance(content, list):
                return '\n'.join(x.get('text', '') for x in content if x.get('type') == 'text')
            return content
        if self.provider == 'anthropic':
            return '\n'.join(x.get('text', '') for x in body.get('content', []) if x.get('type') == 'text')
        candidates = body.get('candidates', [])
        return '\n'.join(x.get('text', '') for x in candidates[0].get('content', {}).get('parts', []) if not x.get('thought')) if candidates else ''

    def call(self, image, prompt, audit_path):
        from cad_agents import APIError
        if self.calls >= self.max_calls:
            raise APIError('Configured API call budget exhausted')
        self.calls += 1
        image, audit_path = Path(image), Path(audit_path)
        mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}.get(image.suffix.lower())
        if mime is None:
            raise ValueError('Unsupported image format: ' + image.suffix)
        pixels = image.read_bytes()
        endpoint, headers, payload = self.request_spec(base64.b64encode(pixels).decode('ascii'), mime, prompt)
        record = {'provider': self.provider, 'model': self.model, 'endpoint': endpoint,
                  'prompt': prompt, 'image_sha256': hashlib.sha256(pixels).hexdigest(), 'call_number': self.calls}
        start = time.monotonic()
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                response = client.post(endpoint, headers=headers, json=payload)
            record['http_status'] = response.status_code
            record['trace_id'] = response.headers.get('x-request-id') or response.headers.get('request-id')
            try:
                raw = response.json()
            except ValueError:
                raise APIError(f'Non-JSON API response (HTTP {response.status_code})') from None
            if not isinstance(raw, dict):
                raise APIError('Provider response must be a JSON object')
            record['provider_response'] = raw
            if not response.is_success or raw.get('error'):
                detail = raw.get('error', {})
                message = detail.get('message', '') if isinstance(detail, dict) else str(detail)
                raise APIError(f'HTTP {response.status_code}: {message}')
            text = self.extract_text(raw)
            if not isinstance(text, str) or not text.strip():
                raise APIError('Provider returned no image understanding content')
            record['response'] = {'content': text, 'base_resp': {'status_code': 0}, 'usage': raw.get('usage', raw.get('usageMetadata'))}
            return text
        except (httpx.HTTPError, APIError) as exc:
            record['error'] = self.redact(exc)
            raise APIError(self.redact(exc)) from None
        finally:
            record['elapsed_seconds'] = round(time.monotonic() - start, 3)
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_text(self.redact(json.dumps(record, ensure_ascii=False, indent=2)), encoding='utf-8')
