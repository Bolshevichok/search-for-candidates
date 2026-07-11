"""Shared HTTP client with unified retry/backoff policy (FR-009, Principle VI)."""

from __future__ import annotations

import time
from typing import Any

import httpx

BACKOFF_SECONDS = (1, 2, 4)
MAX_ATTEMPTS = 3


class HttpClient:
  def __init__(
    self,
    *,
    timeout: float = 10.0,
    request_delay_sec: float = 0.0,
  ) -> None:
    self.timeout = timeout
    self.request_delay_sec = request_delay_sec
    self._client = httpx.Client(timeout=timeout, follow_redirects=True)

  def close(self) -> None:
    self._client.close()

  def __enter__(self) -> HttpClient:
    return self

  def __exit__(self, *args: object) -> None:
    self.close()

  def get(self, url: str, **kwargs: Any) -> httpx.Response:
    return self.request("GET", url, **kwargs)

  def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
      if self.request_delay_sec > 0:
        time.sleep(self.request_delay_sec)
      try:
        response = self._client.request(method, url, **kwargs)
      except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
        last_exc = exc
        if attempt < MAX_ATTEMPTS - 1:
          time.sleep(BACKOFF_SECONDS[attempt])
          continue
        raise
      if 400 <= response.status_code < 500:
        return response
      if response.status_code >= 500:
        if attempt < MAX_ATTEMPTS - 1:
          time.sleep(BACKOFF_SECONDS[attempt])
          continue
      return response
    if last_exc:
      raise last_exc
    raise RuntimeError(f"request failed for {url}")
