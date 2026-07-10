# Quickstart: проверка Core Pipeline Foundation

Как убедиться, что фича работает, без чтения кода. Предполагается Python 3.12+ venv с
зависимостями из `pyproject.toml`/`requirements.txt` (создаётся в рамках задач этой фичи).

## Предусловия

- `data/university_registry.csv` существует и содержит хотя бы несколько вузов с рабочим `domain`
  (готовый файл уже есть в репозитории — `university_registry/README.md`).
- `config.yaml` в корне репозитория со значениями по умолчанию из этой фичи:

```yaml
run:
  layer1: true
  vak: true
  match: true
  layer2: false
  vk: false
limits:
  request_delay_sec: 1.5
  max_universities: 5   # для быстрой проверки; null — для полного прогона
```

## Сценарий 1 — US1: слой 1 не падает на недоступном вузе

```bash
python -m app step layer1 --config config.yaml
python -m app status
```

**Ожидаемо**: в `status` видно `universities_ok` + `universities_error` = `max_universities`; если
среди пробных 5 вузов есть недоступный — он не остановил обход остальных (см. `spec.md`, US1,
Acceptance Scenario 2). Для проверки чекпоинта: прервать команду (Ctrl+C) после 2–3 вузов и
перезапустить — лог не должен показывать повторный запрос к уже обработанным доменам (US1, Scenario
3).

## Сценарий 2 — US2: 4 статуса и `possible_namesakes`

```bash
python -m app step vak --config config.yaml
python -m app step match --config config.yaml
```

**Ожидаемо**: открыть `data/state.sqlite` (например, через `sqlite3` CLI или DB Browser) и
проверить `SELECT match_status, COUNT(*) FROM candidates GROUP BY match_status;` — только 4
значения, без NULL и без пятого. `SELECT * FROM possible_namesakes;` — если реестр содержит вуз с
известной коллизией ФИО, здесь появится строка, а обе связанные карточки в `candidates` сохраняют
свой обычный `match_status` и `needs_review = true` (US2, Acceptance Scenario 3).

## Сценарий 3 — US3: полный `run` без layer2/VK и повторный `export`

```bash
python -m app run --config config.yaml
```

**Ожидаемо**: код возврата 0; в `logs/run_*.log` нет обращений к коду `app/sources/universities/
layer2.py` или `app/vk/`; в `output/` появляется один `candidates_*.xlsx` с листами `candidates`,
`possible_namesakes`, `university_errors`, `run_meta` (структура — `contracts/xlsx-contract.md`); в
листе `candidates` колонки `email`/`phone`/`vk_url`/`vk_score`/`vk_status` существуют, но пустые во
всех строках.

Затем:

```bash
python -m app export --out output/candidates_recheck.xlsx
```

**Ожидаемо**: команда завершается за секунды (без сетевых запросов — только чтение
`data/state.sqlite`), новый файл эквивалентен предыдущему по содержимому `candidates` (US3,
Acceptance Scenario 3 / `spec.md` SC-007).

## Сценарий 4 — FR-016: защита от преждевременного включения layer2/vk

Изменить в `config.yaml` `run.vk: true`, оставив layer2/vk нереализованными, и запустить:

```bash
python -m app run --config config.yaml
```

**Ожидаемо**: команда немедленно завершается ошибкой о нереализованном шаге, до начала layer1/vak
(не частичный/обманчивый xlsx, не тихий пропуск флага).
