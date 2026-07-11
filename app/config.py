"""Load config.yaml and .env; enforce FR-016 fail-fast for unimplemented steps."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
  pass


class StepNotImplementedError(ConfigError):
  pass


@dataclass
class RunFlags:
  layer1: bool = True
  vak: bool = True
  match: bool = True
  layer2: bool = False
  vk: bool = False


@dataclass
class Limits:
  request_delay_sec: float = 1.5
  max_universities: int | None = None
  vak_max_pages: int | None = None
  layer1_workers: int = 4
  vak_request_delay_sec: float = 0.0
  vak_detail_workers: int = 8


@dataclass
class AppConfig:
  run: RunFlags
  limits: Limits
  config_path: Path

  def validate_implemented_steps(self) -> None:
    if self.run.layer2 or self.run.vk:
      raise StepNotImplementedError(
        "NotImplementedError: layer2/vk step is not implemented in this build"
      )


def load_config(config_path: Path | str = "config.yaml") -> AppConfig:
  path = Path(config_path)
  load_dotenv()
  raw: dict[str, Any] = {}
  if path.exists():
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

  run_raw = raw.get("run") or {}
  limits_raw = raw.get("limits") or {}
  max_uni = limits_raw.get("max_universities")
  vak_max_pages = limits_raw.get("vak_max_pages")
  cfg = AppConfig(
    run=RunFlags(
      layer1=bool(run_raw.get("layer1", True)),
      vak=bool(run_raw.get("vak", True)),
      match=bool(run_raw.get("match", True)),
      layer2=bool(run_raw.get("layer2", False)),
      vk=bool(run_raw.get("vk", False)),
    ),
    limits=Limits(
      request_delay_sec=float(limits_raw.get("request_delay_sec", 1.5)),
      max_universities=int(max_uni) if max_uni is not None else None,
      vak_max_pages=int(vak_max_pages) if vak_max_pages is not None else None,
      layer1_workers=int(limits_raw.get("layer1_workers", 4)),
      vak_request_delay_sec=float(limits_raw.get("vak_request_delay_sec", 0.0)),
      vak_detail_workers=int(limits_raw.get("vak_detail_workers", 8)),
    ),
    config_path=path,
  )
  cfg.validate_implemented_steps()
  return cfg
