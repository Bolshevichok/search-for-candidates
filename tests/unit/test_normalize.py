"""Tests for FIO and organization normalization."""

from app.matching.normalize import normalize_fio, normalize_organization


def test_normalize_fio_case_and_yo():
  assert normalize_fio("Иванов Иван Иванович") == "иванов иван иванович"
  assert normalize_fio("Сёмов Пётр") == "семов петр"


def test_normalize_fio_whitespace_and_hyphen():
  assert normalize_fio("  Петров  -  Водкин   Алексей  ") == "петров-водкин алексей"
  assert normalize_fio("Иванов   Иван") == "иванов иван"


def test_normalize_organization():
  assert normalize_organization("  Уральский   федеральный  университет  ") == (
    "уральский федеральный университет"
  )
  assert normalize_organization("Ёлка") == "елка"
