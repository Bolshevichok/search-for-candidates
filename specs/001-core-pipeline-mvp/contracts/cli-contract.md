# Contract: CLI команды

Единственный внешний интерфейс этой фичи для оператора — консольные команды пакета `app` (запуск
`python -m app <command>`). Все команды читают/пишут только `data/state.sqlite`,
`data/university_registry.csv` (только чтение) и `config.yaml`/`.env`.

## `run [--config config.yaml] [--full]`

Выполняет `layer1 → vak → match → export` последовательно, согласно флагам в `config.yaml`.

- Без `--full`: продолжает незавершённый `run` (см. `data-model.md`, §runs/run_steps), если такой
  есть; иначе начинает новый.
- С `--full`: форсирует полный пересбор всех источников независимо от прошлых чекпоинтов.
- **Вход**: `config.yaml` с блоком `run: {layer1, vak, match, layer2, vk}` (bool) и `limits:
  {request_delay_sec, max_universities}`.
- **Выход**: код возврата 0 при успехе; ненулевой — при фатальной ошибке (не при отдельных ошибках
  по вузам — те уходят в `university_errors`, run продолжается).
- **Предусловие FR-016**: если `run.layer2` или `run.vk` в конфиге `true` — команда завершается
  немедленно с ошибкой `NotImplementedError: layer2/vk step is not implemented in this build` до
  начала выполнения любого шага.

## `step layer1|vak|match`

Выполняет один шаг изолированно (для отладки — например, «ВАК уже выгружен, сайты ещё нет»).
Использует тот же чекпоинт-механизм `runs`/`run_steps`, что и `run`.

## `export [--out output/candidates.xlsx]`

Формирует xlsx из текущего состояния `data/state.sqlite` **без** сетевых запросов — только чтение
БД (см. `SC-007` в `spec.md`). Контракт содержимого файла — `xlsx-contract.md`.

## `status`

Печатает в stdout: количество вузов `ok`/`error` в `layer1_status`, число записей в
`employees_raw`/`vak_raw`/`candidates`, размер `data/state.sqlite`, был ли последний `run`
завершён или прерван.

## `reset`

Явный полный сброс `data/state.sqlite`. Перед удалением делает автоматический бэкап в
`data/backups/state_<run_id>.sqlite` (FR-014). Требует явного вызова — не вызывается ни из `run`,
ни из `step`.
