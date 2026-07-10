"""Tests for identity_key construction and disciplines merge."""

from app.matching.identity_key import (
  build_identity_key,
  identity_keys_equivalent,
  merge_disciplines,
)


def test_build_identity_key():
  key = build_identity_key("иванов иван", 5, "dept-1", 10, 8)
  assert key == "иванов иван|5|dept-1|10"


def test_identity_key_experience_tolerance():
  left = build_identity_key("иванов иван", 1, "d", 10, None)
  right = build_identity_key("иванов иван", 1, "d", 11, None)
  assert identity_keys_equivalent(left, right, tolerance=2)
  assert not identity_keys_equivalent(left, build_identity_key("иванов иван", 1, "d", 14, None))


def test_merge_disciplines_union_without_duplicates():
  merged = merge_disciplines(
    ["Информатика", "Математика"],
    ["Математика", "Программирование"],
  )
  assert merged == ["Информатика", "Математика", "Программирование"]
