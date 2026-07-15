"""Shared HTTP client with unified retry/backoff policy (FR-009, Principle VI)."""

from __future__ import annotations

import ssl
import time
from typing import Any

import httpx

BACKOFF_SECONDS = (1, 2, 4)
MAX_ATTEMPTS = 3

# A real browser's default headers. Bare python-httpx requests (no UA,
# no Accept) are an easy signal for anti-bot/DDoS protection (Qrator,
# DDoS-Guard and similar, common in front of RU gov/edu sites) to drop --
# which can show up as a mid-handshake ConnectError, not just a 403.
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
  """Fallback for genuinely old servers (old Apache/OpenSSL on some RU
  university sites) that do unsafe legacy TLS renegotiation, which
  OpenSSL 3.x refuses by default -- surfacing as
  `ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]`.

  Deliberately NOT the default: lowering the cipher security level also
  makes the TLS ClientHello fingerprint (JA3) less browser-like, which
  trips anti-bot protection on modern sites (e.g. Qrator-fronted
  gov/edu APIs) that otherwise connect fine with a normal context. Only
  used as a second attempt after a plain handshake fails.
  """
  ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
  ctx.check_hostname = False
  ctx.verify_mode = ssl.CERT_NONE
  ctx.set_ciphers("DEFAULT@SECLEVEL=1")
  ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
  return ctx


def _is_ssl_handshake_error(exc: Exception) -> bool:
  return isinstance(exc, httpx.ConnectError) and "SSL" in str(exc)


class HttpClient:
  def __init__(
    self,
    *,
    timeout: float = 10.0,
    request_delay_sec: float = 0.0,
    verify_ssl: bool = False,
  ) -> None:
    self.timeout = timeout
    self.request_delay_sec = request_delay_sec
    self.verify_ssl = verify_ssl
    self._client = self._build_client(_default_ssl_context(verify_ssl))
    self._legacy_client: httpx.Client | None = None

  def _build_client(self, ssl_context: ssl.SSLContext) -> httpx.Client:
    return httpx.Client(
      timeout=self.timeout,
      follow_redirects=True,
      verify=ssl_context,
      headers=_BROWSER_HEADERS,
      # Ignore HTTP_PROXY/HTTPS_PROXY etc. A local proxy (often an
      # antivirus doing HTTPS inspection, e.g. Kaspersky/ESET) can sit in
      # the middle and break the TLS handshake before it even reaches the
      # target site, surfacing as the same
      # ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING] -- but from
      # httpcore's http_proxy transport, not the site itself.
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
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
      if self.request_delay_sec > 0:
        time.sleep(self.request_delay_sec)
      client = self._client
      # After a plain handshake fails with an SSL error once, retry the
      # remaining attempts against the same host with the legacy-permissive
      # context instead -- covers the old-server case without weakening
      # every single request up front.
      if last_exc is not None and _is_ssl_handshake_error(last_exc):
        if self._legacy_client is None:
          self._legacy_client = self._build_client(_legacy_ssl_context(self.verify_ssl))
        client = self._legacy_client
      try:
        response = client.request(method, url, **kwargs)
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
