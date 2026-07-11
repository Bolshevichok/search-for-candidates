"""Probe Yandex API host endpoints."""

import httpx

urls = [
    'https://llm.api.cloud.yandex.net/',
    'https://llm.api.cloud.yandex.net/v1',
    'https://llm.api.cloud.yandex.net/llm/v1',
    'https://llm.api.cloud.yandex.net/llm',
    'https://api.cloud.yandex.net/',
    'https://api.cloud.yandex.net/llm/v1',
]

for url in urls:
    try:
        r = httpx.get(url, timeout=10)
        print(url, r.status_code, repr(r.text[:200]))
    except Exception as e:
        print(url, 'ERR', type(e).__name__, e)
