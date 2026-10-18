import json
from pathlib import Path

import httpx
import pytest

from cad_agents import APIError, create_vision_client
from cad_providers import ProviderVision


RESPONSES = {
    'openai_responses': {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"ok":true}'}]}]},
    'openai_chat': {'choices': [{'message': {'content': '{"ok":true}'}}]},
    'openai_compatible': {'choices': [{'message': {'content': '{"ok":true}'}}]},
    'anthropic': {'content': [{'type': 'text', 'text': '{"ok":true}'}]},
    'gemini': {'candidates': [{'content': {'parts': [{'thought': True, 'text': 'private thought'}, {'text': '{"ok":true}'}]}}]},
}


def configured(monkeypatch, provider):
    monkeypatch.setenv('LLM_PROVIDER', provider)
    monkeypatch.setenv('LLM_MODEL', 'vision-test-model')
    monkeypatch.setenv('LLM_API_KEY', 'test-secret-never-publish-123456')
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.test/v1')
    return create_vision_client(max_calls=1)


@pytest.mark.parametrize('provider', RESPONSES)
def test_provider_wire_format_and_audit_without_secrets(provider, monkeypatch, tmp_path):
    adapter = configured(monkeypatch, provider)
    seen = []
    def handle(request):
        seen.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, json=RESPONSES[provider])
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    image = tmp_path / 'image.jpg'
    image.write_bytes(b'unique-image-payload')
    audit = tmp_path / 'audit.json'
    assert adapter.call(image, 'classify', audit) == '{"ok":true}'
    url, headers, body = seen[0]
    if provider == 'gemini':
        assert 'key=' not in url and headers['x-goog-api-key'] == adapter.key
        assert 'inline_data' in body['contents'][0]['parts'][1]
    elif provider == 'anthropic':
        assert headers['x-api-key'] == adapter.key and body['messages'][0]['content'][0]['type'] == 'image'
    elif provider == 'openai_responses':
        assert body['store'] is False and body['input'][0]['content'][1]['type'] == 'input_image'
    else:
        assert body['messages'][0]['content'][1]['type'] == 'image_url'
    record = audit.read_text(encoding='utf-8')
    assert adapter.key not in record and 'dW5pcXVlLWltYWdlLXBheWxvYWQ=' not in record
    assert json.loads(record)['response']['content'] == '{"ok":true}'
    with pytest.raises(APIError, match='budget'):
        adapter.call(image, 'classify', tmp_path / 'again.json')
    assert len(seen) == 1


@pytest.mark.parametrize('status,body', [(429, {'error': {'message': 'rate limit'}}), (200, {'choices': []}), (401, {'error': {'message': 'invalid API key'}})])
def test_provider_failure_is_audited_without_fallback(status, body, monkeypatch, tmp_path):
    adapter = configured(monkeypatch, 'openai_chat')
    original = httpx.Client
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(status, json=body)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    image = tmp_path / 'image.png'; image.write_bytes(b'image')
    audit = tmp_path / 'audit.json'
    with pytest.raises(APIError):
        adapter.call(image, 'prompt', audit)
    assert len(calls) == 1 and json.loads(audit.read_text())['http_status'] == status


@pytest.mark.parametrize('url', ['http://remote.test/v1', 'https://user:password@example.test/v1', 'https://example.test/v1?key=secret'])
def test_provider_rejects_unsafe_endpoint_config(url, monkeypatch):
    configured(monkeypatch, 'openai_chat')
    monkeypatch.setenv('LLM_BASE_URL', url)
    with pytest.raises(ValueError):
        ProviderVision()


def test_other_provider_never_queries_minimax_quota(monkeypatch):
    from cad_benchmark import query_quota
    from types import SimpleNamespace
    monkeypatch.setattr(httpx, 'get', lambda *args, **kwargs: pytest.fail('Unexpected MiniMax quota request'))
    query_quota(Path('unused'), SimpleNamespace(redactor=SimpleNamespace(provider='openai_responses')))
