# Implementation Plan: Core Pipeline Foundation (Layer 1 + VAK + Matcher, no Layer 2 / VK)

**Branch**: `001-core-pipeline-mvp` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-core-pipeline-mvp/spec.md`

## Summary

Собрать первую рабочую половину пайплайна: слой 1 (сотрудники сайтов вузов из
`/sveden/employees`), выгрузка ВАК (list + **detail** на каждую запись) и матчер, объединяющий их в
единую карточку кандидата с одним из 4 статусов. Ingest: **layer1 ∥ VAK** (`app/pipeline/ingest.py`);
внутри — параллель по вузам (`layer1_workers`) и parallel detail fetch (`vak_detail_workers`).
Экспорт — два листа людей (`site_employees`, `vak_candidates`). Слой 2 и VK — заготовки без логики.

## Technical Context

**Language/Version**: Python 3.12+ (`app-architecture.md`, §3)

**Primary Dependencies**: `httpx` (HTTP-клиент для ВАК API и сайтов вузов), `lxml` + `selectolax`
(парсинг `itemprop`-микроразметки), `openpyxl` (xlsx-экспорт), `PyYAML` (`config.yaml`),
`python-dotenv` (`.env`), `rapidfuzz` (fuzzy-сравнение названий вузов/подразделений — MIT-лицензия,
без GPL-рисков `fuzzywuzzy`); CLI — стандартный `argparse` с подкомандами (`run`/`step`/`export`/
`status`/`reset`), без `typer`/`click`, чтобы не тащить лишнюю зависимость туда, где хватает stdlib
(Принцип I конституции — Simplicity First)

**Storage**: SQLite, один файл `data/state.sqlite` (`app-architecture.md`, §3, §5)

**Testing**: `pytest`, только unit-тесты на чистую логику без сети — нормализация ФИО/организации,
`identity_key`, матчер (4 статуса + `possible_namesakes`), хэши (`candidate_content_hash`,
`university_site_hash`), xlsx-контракт колонок. Без контрактных/интеграционных тестов, требующих
живую сеть (ВАК API, сайты вузов) — они нестабильны и не под нашим контролем; ручная проверка
сквозного прогона — через `quickstart.md`. Обоснование решения — см. `research.md`

**Target Platform**: ноутбук/ВМ оператора с Python venv, без привязки к ОС (разрабатывается на
Windows, должно работать и на Linux — без os-specific вызовов кроме путей через `pathlib`)

**Project Type**: единый проект — консольный пакет (`app-architecture.md`, §1: батч-CLI, не веб)

**Performance Goals**: на реестре из ~1000 вузов первый полный прогон слоя 1 — часы (`layer1_workers`,
по умолчанию 4 параллельных домена), не сутки; ingest wall-clock ≈ **max(layer1, vak)**, не сумма.
Полная выгрузка ВАК (~164k detail-запросов) — ориентир **десятки минут** при `vak_detail_workers=8`
(smoke5: 2865 detail ≈ 4 мин); обе ветки `is_pilot` для pilot-вузов. Точные числа — см.
`app-architecture.md`, §8.

**Constraints**: задержка 1–2 сек на запрос к одному домену вуза; `layer1_workers` параллельно по разным
доменам; VAK list — последовательно, detail — параллельно (`vak_detail_workers`, по умолчанию 8);
таймаут ВАК API 8–10 сек/запрос; до 3 повторов с backoff 1с→2с→4с на сеть/5xx (Принцип VI).

**Scale/Scope**: ~1000 вузов в `university_registry.csv`, ~164 045 записей ВАК (`is_pilot=false`) +
~20 190 (`is_pilot=true`); порядок кандидатов — десятки-сотни тысяч сотрудников вузов после дедупа

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Принцип | Оценка | Как соблюдается |
|---|---|---|
| I. Simplicity First | PASS | Ровно тот стек, что уже зафиксирован в конституции (§Technology Constraints); ни одной новой абстракции сверх layer1/vak/matcher/db/export/cli; layer2/vk — пустые заготовки без логики (FR-015), не «мы придумали структуру заранее на всякий случай» — они уже названы в существующей архитектуре |
| II. Batch CLI, Not a Service | PASS | Только CLI (`run`/`step`/`export`/`status`/`reset`), результат — файл; ни сервера, ни UI не вводится |
| III. Idempotent, Checkpointed Runs | PASS | FR-008: чекпоинт по вузу (layer1) и по странице/курсору (VAK); прерванный `run` продолжает, не начинает с нуля |
| IV. Sites Are Source of Truth, VAK Additive | PASS | FR-004–FR-007: 4 статуса без исключений, `vak_no_site`/`site_no_vak` не выбрасываются, «вероятные тёзки» — отдельная связь-таблица, не 5-й статус |
| V. Mandatory Enrichment, No Feedback Loop | PASS | Принцип (v1.0.1) явно оговаривает: обязательность layer2/VK применяется к полному (M3) прогону *после* их реализации; милстоуны M0–M2 без layer2/VK — легитимны, если не вводят per-record approval gate и не молчат при попытке включить нереализованный шаг (FR-016 это и обеспечивает) |
| VI. One Retry Policy | PASS | FR-009: один и тот же retry/backoff для ВАК API и сайтов вузов, реализован в одном общем клиенте, не по одному на источник |

Нарушений, требующих записи в Complexity Tracking, нет — таблица ниже не заполняется.

**Post-Phase-1 re-check** (после `data-model.md`/`contracts/`/`quickstart.md`): PASS, без новых
отклонений. В частности, `data-model.md` явно **не** создаёт таблицы `contacts`/`vk_hits` под
layer2/VK (Принцип I — не строить схему под нереализованную логику), а `contracts/xlsx-contract.md`
подтверждает, что колонки layer2/VK в xlsx присутствуют пустыми, не отсутствуют (FR-012/SC-005), то
есть Принцип IV/V по-прежнему соблюдены на уровне контракта файла.

## Project Structure

### Documentation (this feature)

```text
specs/001-core-pipeline-mvp/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
app/
├── cli.py                       # argparse: run / step layer1|vak|match / export / status / reset
├── config.py                    # чтение config.yaml + .env, дефолты layer2=false, vk=false
├── pipeline/
│   └── ingest.py                # layer1 ∥ vak до match (ThreadPoolExecutor)
├── registry/
│   └── loader.py                # чтение data/university_registry.csv → строки universities
├── sources/
│   ├── http_client.py           # единый retry/backoff для ВАК и сайтов
│   ├── vak/
│   │   ├── client.py             # list + detail /api/att/adverts/{id}/
│   │   ├── pipeline.py           # пагинация list, parallel detail fetch
│   │   └── parser.py             # merge list stub + detail → VakRecord
│   └── universities/
│       ├── layer1.py             # /sveden/employees → program pages → itemprop; parallel per uni
│       ├── struct.py             # /sveden/struct → канонический department_id
│       └── layer2.py             # ЗАГОТОВКА: контракт §6.6, без реализации
├── matching/
│   ├── normalize.py              # ФИО/организация
│   ├── identity_key.py           # fio + university_id + department_id + стаж (±2 года)
│   ├── employee_merge.py         # дедуп сотрудников внутри вуза (FR-003)
│   └── matcher.py                # 4 статуса + possible_namesakes
├── vk/
│   └── __init__.py               # ЗАГОТОВКА: MVP-контракт vk-matching-spec.md
├── export/
│   └── xlsx.py                   # site_employees + vak_candidates + служебные листы
└── db/
    ├── schema.sql
    └── repository.py              # CRUD + чекпоинты, WAL + busy_timeout

data/
├── university_registry.csv        # уже существует, только читаем
├── state.sqlite                   # gitignore, создаётся при первом run
├── backups/                       # gitignore, авто-бэкапы (FR-014)
└── raw/                           # gitignore, опциональный кэш HTML/JSON ответов

output/
└── candidates_*.xlsx              # gitignore, финальный артефакт

logs/
└── run_*.log                      # gitignore

tests/
└── unit/
    ├── test_normalize.py
    ├── test_identity_key.py
    ├── test_matcher.py
    └── test_xlsx_contract.py

config.yaml                        # layer1/vak/match: true, layer2/vk: false (эта фича)
.env.example                       # плейсхолдер под будущий VK_TOKEN (не используется в этой фиче)
```

**Structure Decision**: Единый проект (Option 1 из шаблона), без `backend/`+`frontend`/`api`+`ios` —
это консольный пакет `app/`, как и зафиксировано в конституции и `app-architecture.md`, §4. Структура
папок выше — это тот же layout из §4 архитектуры, с добавлением `sources/universities/struct.py`
(канонический `department_id`, нужен и layer1 для дедупа, и будущему layer2 для discovery — общий
модуль, а не дублирование в двух местах) и `tests/unit/` (см. Technical Context → Testing).

## Complexity Tracking

*Нет нарушений Constitution Check — таблица не заполняется.*
