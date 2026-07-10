"""VAK API client with pagination and checkpointing."""

from __future__ import annotations

from typing import Any, Iterator

from app.sources.http_client import HttpClient

VAK_BASE_URL = "https://vak.gisnauka.ru"
PAGE_SIZE = 100


class VakClient:
  def __init__(self, client: HttpClient) -> None:
    self.client = client

  def iter_pages(
    self,
    *,
    is_pilot: bool,
    start_page: int = 1,
  ) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    page = start_page
    while True:
      url = (
        f"{VAK_BASE_URL}/api/att/adverts/?page={page}"
        f"&pageSize={PAGE_SIZE}&is_pilot={'true' if is_pilot else 'false'}"
      )
      response = self.client.get(url)
      if response.status_code != 200:
        raise RuntimeError(f"VAK API HTTP {response.status_code} on page {page}")
      payload = response.json()
      results = payload.get("results") or payload.get("data") or []
      if not results:
        break
      yield page, results
      if not payload.get("next"):
        if len(results) < PAGE_SIZE:
          break
      page += 1
