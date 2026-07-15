"""Shared HTTP client with unified retry/backoff policy (FR-009, Principle VI)."""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, retry_if_result, stop_after_attempt, wait_exponential

_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)


def _is_server_error(response: httpx.Response) -> bool:
  return response.status_code >= 500


def _return_last_result(retry_state: Any) -> httpx.Response:
  return retry_state.outcome.result()


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
    self._retrying = Retrying(
      stop=stop_after_attempt(3),
      wait=wait_exponential(multiplier=1, min=1, max=4),
      retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS) | retry_if_result(_is_server_error),
      retry_error_callback=_return_last_result,
      reraise=True,
    )

  def close(self) -> None:
    self._client.close()

  def __enter__(self) -> HttpClient:
    return self

  def __exit__(self, *args: object) -> None:
    self.close()

  def get(self, url: str, **kwargs: Any) -> httpx.Response:
    return self.request("GET", url, **kwargs)

  def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
    return self._retrying(self._request, method, url, **kwargs)

  def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
    if self.request_delay_sec > 0:
      time.sleep(self.request_delay_sec)
    return self._client.request(method, url, **kwargs)
