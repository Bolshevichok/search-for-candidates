from __future__ import annotations

import ssl
import time
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, retry_if_result, stop_after_attempt, wait_exponential

from app.pipeline.cancellation import CancellationToken

_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)

_BROWSER_HEADERS = {
  "User-Agent": (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
  ),
  "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _default_ssl_context(verify_ssl: bool) -> ssl.SSLContext:
  ctx = ssl.create_default_context()
  if not verify_ssl:
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
  return ctx


def _legacy_ssl_context(verify_ssl: bool) -> ssl.SSLContext:
  ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
  ctx.check_hostname = False
  ctx.verify_mode = ssl.CERT_NONE
  ctx.set_ciphers("DEFAULT@SECLEVEL=1")
  ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
  return ctx


def _is_ssl_handshake_error(exc: Exception) -> bool:
  return isinstance(exc, httpx.ConnectError) and "SSL" in str(exc)


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
    verify_ssl: bool = False,
    cancel_token: CancellationToken | None = None,
  ) -> None:
    self.timeout = timeout
    self.request_delay_sec = request_delay_sec
    self.verify_ssl = verify_ssl
    self.cancel_token = cancel_token
    self._client = self._build_client(_default_ssl_context(verify_ssl))
    self._legacy_client: httpx.Client | None = None
    self._retrying = Retrying(
      stop=stop_after_attempt(3),
      wait=wait_exponential(multiplier=1, min=1, max=4),
      retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS) | retry_if_result(_is_server_error),
      retry_error_callback=_return_last_result,
      reraise=True,
    )

  def _build_client(self, ssl_context: ssl.SSLContext) -> httpx.Client:
    return httpx.Client(
      timeout=self.timeout,
      follow_redirects=True,
      verify=ssl_context,
      headers=_BROWSER_HEADERS,
      trust_env=False,
    )

  def close(self) -> None:
    self._client.close()
    if self._legacy_client is not None:
      self._legacy_client.close()

  def __enter__(self) -> HttpClient:
    return self

  def __exit__(self, *args: object) -> None:
    self.close()

  def get(self, url: str, **kwargs: Any) -> httpx.Response:
    return self.request("GET", url, **kwargs)

  def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
    legacy_client: httpx.Client | None = None

    def send() -> httpx.Response:
      nonlocal legacy_client
      if self.cancel_token is not None:
        self.cancel_token.check()
      if self.request_delay_sec > 0:
        if self.cancel_token is not None:
          self.cancel_token.wait(self.request_delay_sec)
        else:
          time.sleep(self.request_delay_sec)
      if self.cancel_token is not None:
        self.cancel_token.check()
      try:
        return (legacy_client or self._client).request(method, url, **kwargs)
      except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
        if legacy_client is None and _is_ssl_handshake_error(exc):
          if self._legacy_client is None:
            self._legacy_client = self._build_client(_legacy_ssl_context(self.verify_ssl))
          legacy_client = self._legacy_client
        raise

    return self._retrying(send)
