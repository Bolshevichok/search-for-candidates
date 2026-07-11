"""FIO and organization name normalization (candidate-pipeline-architecture.md §4.1)."""

from __future__ import annotations

import re

_YO_MAP = str.maketrans({"ё": "е", "Ё": "Е"})
_SPACE_RE = re.compile(r"\s+")
_HYPHEN_SPACES_RE = re.compile(r"\s*-\s*")


def normalize_fio(value: str) -> str:
  text = value.strip().translate(_YO_MAP)
  text = _HYPHEN_SPACES_RE.sub("-", text)
  text = _SPACE_RE.sub(" ", text)
  return text.casefold()


def normalize_organization(value: str) -> str:
  text = value.strip().translate(_YO_MAP)
  text = _SPACE_RE.sub(" ", text)
  return text.casefold()


def split_fio_parts(value: str) -> tuple[str, str, str | None]:
  parts = normalize_fio(value).split()
  if len(parts) < 2:
    return parts[0] if parts else "", "", None
  last = parts[0]
  first = parts[1]
  patronymic = parts[2] if len(parts) > 2 else None
  return last, first, patronymic
