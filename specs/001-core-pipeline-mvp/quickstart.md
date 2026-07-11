# Quickstart: проверка Core Pipeline MVP

Как убедиться, что фича работает, без чтения кода.

## Предусловия

- Python 3.12+ venv с зависимостями из `pyproject.toml`.
- `data/university_registry.csv` — в репозитории; у записей для layer1 нужен непустой `domain`.
- Конфиги: `config.yaml` (полный прогон), `config.smoke5.yaml` (smoke: 5 вузов, 15 страниц VAK).

```yaml
# config.smoke5.yaml (фрагмент)
run:
  layer1: true
  vak: true
  match: true
  layer2: false
  vk: false
limits:
  request_delay_sec: 1.0
  max_universities: 5
  vak_max_pages: 15
  layer1_workers: 4
  vak_detail_workers: 8
```

## Smoke-прогон (рекомендуется)

```bash
python -m app reset
python -m app --config config.smoke5.yaml run --out output/smoke5.xlsx --full
python -m app status
```

**Ожидаемо:** ~4–5 мин; `output/smoke5.xlsx` с листами `site_employees`, `vak_candidates`, `possible_namesakes`, `university_errors`, `run_meta`. Layer1 и VAK идут параллельно до match.

## Сценарий 1 — layer1 и ошибки реестра

```bash
python -m app step layer1 --config config.smoke5.yaml
python -m app status
```

**Ожидаемо:** вузы с пустым `domain` → `unresolved_domain` (HTTP не вызывался); остальные обрабатываются параллельно; недоступный вуз не останавливает обход.

## Сценарий 2 — VAK detail + match

```bash
python -m app step vak --config config.smoke5.yaml
python -m app step match --config config.smoke5.yaml
```

**Ожидаемо:** `vak_raw` с заполненными specialty/branch/defend_org; `candidates` — только 4 значения `match_status`.

## Сценарий 3 — export без сети

```bash
python -m app export --out output/recheck.xlsx
```

**Ожидаемо:** секунды, без HTTP; два листа людей (`site_employees`, `vak_candidates`).

## Сценарий 4 — FR-016

Включить `run.vk: true` при нереализованном vk → `run` падает до ingest.

## Что смотреть в xlsx

- **`site_employees`:** post, academic_title, spec_experience, source_url; без дублей ФИО+вуз.
- **`vak_candidates`:** specialty_code/name, branch, org_address/phone.
- **`university_errors`:** `unresolved_domain` = дописать domain в CSV, не проблема сайта.

Контракт листов: `specs/001-core-pipeline-mvp/contracts/xlsx-contract.md`.
