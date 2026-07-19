
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class Limits:
  request_delay_sec: float = 1.5
  max_universities: int | None = None
  vak_max_pages: int | None = None
  layer1_workers: int = 4
  vak_request_delay_sec: float = 0.0
  vak_detail_workers: int = 8
  layer2_workers: int = 2
  layer2_request_delay_sec: float = 2.0
  layer2_limit: int = 350
  layer2_blocked_domain_keywords: list[str] = field(default_factory=list)
  # True: every layer2 page goes through Crawl4AI first (old, slow behavior).
  # False (default): HTTP-first, browser only for failures/challenges/JS shells.
  layer2_prefer_browser: bool = False
  layer2_max_fetches_per_candidate: int = 40


@dataclass
class AppConfig:
  limits: Limits


def load_config(config_path: Path | str = "config.yaml") -> AppConfig:
  path = Path(config_path)
  load_dotenv()
  raw: dict[str, Any] = {}
  if path.exists():
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

  limits_raw = raw.get("limits") or {}
  max_uni = limits_raw.get("max_universities")
  vak_max_pages = limits_raw.get("vak_max_pages")
  cfg = AppConfig(
    limits=Limits(
      request_delay_sec=float(limits_raw.get("request_delay_sec", 1.5)),
      max_universities=int(max_uni) if max_uni is not None else None,
      vak_max_pages=int(vak_max_pages) if vak_max_pages is not None else None,
      layer1_workers=int(limits_raw.get("layer1_workers", 4)),
      vak_request_delay_sec=float(limits_raw.get("vak_request_delay_sec", 0.0)),
      vak_detail_workers=int(limits_raw.get("vak_detail_workers", 8)),
      layer2_workers=int(limits_raw.get("layer2_workers", 2)),
      layer2_request_delay_sec=float(limits_raw.get("layer2_request_delay_sec", 2.0)),
      layer2_limit=int(limits_raw.get("layer2_limit", 100)),
      layer2_blocked_domain_keywords=list(limits_raw.get("layer2_blocked_domain_keywords", [])),
      layer2_prefer_browser=bool(limits_raw.get("layer2_prefer_browser", False)),
      layer2_max_fetches_per_candidate=int(limits_raw.get("layer2_max_fetches_per_candidate", 40)),
    ),
  )
  return cfg
