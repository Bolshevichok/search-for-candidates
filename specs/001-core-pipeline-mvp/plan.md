# Implementation Plan: Core Pipeline Foundation (Layer 1 + VAK + Matcher, no Layer 2 / VK)

**Branch**: `001-core-pipeline-mvp` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-core-pipeline-mvp/spec.md`

## Summary

Собрать первую рабочую половину пайплайна: слой 1 (сотрудники сайтов вузов из
`/sveden/employees`), выгрузка ВАК (объявления о защитах) и матчер, объединяющий их в единую
карточку кандидата с одним из 4 статусов совпадения. Всё это — один консольный `run`, идемпотентный
по чекпоинтам в `data/state.sqlite`, с экспортом в `output/candidates_*.xlsx`. Слой 2 (контакты) и
VK — пустые заготовки-модули без логики, не подключённые к CLI; `run`/`export` работают полноценно
без них (`layer2: false`, `vk: false` по умолчанию). Технический подход — прямая реализация того,
что уже зафиксировано в `research/pipeline/app-architecture.md` (M0+M1) и
`research/pipeline/candidate-pipeline-architecture.md` (§1–5, §8 частично), без новой архитектуры.

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

**Performance Goals**: на реестре из ~1000 вузов (см. `data/university_registry.csv`) первый полный
прогон слоя 1 — часы (параллелизм ~10 доменов), не сутки; полная выгрузка ВАК (~164 045 записей,
`is_pilot=false`) через постраничный API — десятки минут при 5–10 параллельных запросах; для вузов с
`is_pilot=true` — дополнительный проход по второй ветке (~20 190 записей). Точные числа не
подтверждены на реальных данных (см. `app-architecture.md`, §8) — цель этой фичи не побить SLA, а
пройти весь реестр без ручной остановки и без обвала на отдельных вузах

**Constraints**: задержка 1–2 сек на запрос к одному домену вуза, ~10 параллельных доменов
одновременно (сайты независимы); таймаут ВАК API 8–10 сек/запрос (сервер сам по себе медленный —
это норма); до 3 повторов с backoff 1с→2с→4с на сеть/5xx, без повтора на 4xx (Принцип VI конституции)

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
├── registry/
│   └── loader.py                # чтение data/university_registry.csv → строки universities
├── sources/
│   ├── vak/
│   │   ├── client.py             # httpx-клиент /api/att/adverts/, retry/backoff, is_pilot=both
│   │   └── parser.py             # маппинг JSON-полей API → VakRecord
│   └── universities/
│       ├── layer1.py             # /sveden/employees (индекс) → страницы программ → itemprop → EmployeeRaw
│       ├── struct.py             # /sveden/struct → канонические department_id (используется layer1 и будущим layer2)
│       └── layer2.py             # ЗАГОТОВКА: докстринг с контрактом §6.6, без реализации, не вызывается из cli.py
├── matching/
│   ├── normalize.py              # ФИО/организация: регистр, ё→е, пробелы, дефисы, алиасы вуза
│   ├── identity_key.py           # normalized_fio + university_id + department_id + стаж (±1-2 года)
│   └── matcher.py                # 4 статуса + possible_namesakes
├── vk/
│   └── __init__.py               # ЗАГОТОВКА: докстринг с MVP-контрактом vk-matching-spec.md, без реализации
├── export/
│   └── xlsx.py                   # листы candidates / possible_namesakes / university_errors / run_meta
└── db/
    ├── schema.sql                 # DDL всех таблиц (см. data-model.md)
    └── repository.py              # CRUD + чекпоинты по runs/run_steps

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
