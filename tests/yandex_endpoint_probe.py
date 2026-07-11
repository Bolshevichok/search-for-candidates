"""Probe candidate Yandex LLM endpoints."""

import httpx

urls = [
    'https://llm.api.cloud.yandex.net/llm/v1alpha/chat/completion',
    'https://llm.api.cloud.yandex.net/llm/v1/chat/completions',
    'https://llm.api.cloud.yandex.net/llm/v1/text:generate',
    'https://llm.api.cloud.yandex.net/llm/v1/complete',
    'https://llm.api.cloud.yandex.net/llm/v1beta2/chat/completions',
]

for url in urls:
    try:
        r = httpx.head(url, timeout=10)
        print(url, r.status_code, r.headers.get('content-type'))
    except Exception as e:
        print(url, 'ERR', type(e).__name__, e)
