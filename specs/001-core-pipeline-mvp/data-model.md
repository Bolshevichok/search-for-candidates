# Data Model: Core Pipeline Foundation (Layer 1 + VAK + Matcher)

Схема — рабочий склад одного прогона (`data/state.sqlite`), не продуктовая БД (см.
`app-architecture.md`, §5). В этой фиче создаются только таблицы, которые реально заполняются
layer1/vak/match/export; `contacts` и `vk_hits` из полной архитектуры сюда **не входят** — их
создаёт будущая фича, когда появится код, который их пишет и читает (Принцип I — не создавать схему
под то, что не реализовано).

## `universities`

Загружается из `data/university_registry.csv` при старте `run` (не пересобирается этой фичей).

| Поле | Тип | Обязательность | Комментарий |
|---|---|---|---|
| `university_id` | INTEGER PK | — | surrogate, стабилен между прогонами по `domain` |
| `official_name` | TEXT | NOT NULL | из реестра Рособрнадзора |
| `aliases` | TEXT (JSON-массив) | NULL | для fuzzy-сопоставления с `defend_org` (§4.1) |
| `domain` | TEXT | NOT NULL, UNIQUE | без протокола, напр. `urfu.ru` |
| `region` | TEXT | NULL | |
| `accreditation_status` | TEXT | NULL | |
| `vk_group_id` | TEXT | NULL | не используется в этой фиче, только хранится |
| `is_pilot` | BOOLEAN | NOT NULL, DEFAULT false | триггерит запрос обеих веток ВАК (FR-004) |
| `layer1_status` | TEXT | NULL | `ok` / `unreachable` / `unresolved_domain` — обновляется layer1 |

## `employees_raw`

Сырые записи со страниц образовательных программ `/sveden/employees` (после дедупа по
`identity_key` — см. FR-003; одна строка = один человек в одном вузе, не одна строка на программу).

| Поле | Тип | Обязательность | Комментарий |
|---|---|---|---|
| `employee_id` | INTEGER PK | — | |
| `university_id` | FK → universities | NOT NULL | |
| `fio` | TEXT | NOT NULL | как на странице, до нормализации |
| `fio_normalized` | TEXT | NOT NULL | регистр, `ё→е`, пробелы, дефисы — вход в `identity_key` |
| `post` | TEXT | NULL | `itemprop="post"` |
| `degree` | TEXT | NULL | `itemprop="degree"` |
| `academic_title` | TEXT | NULL | `itemprop="academStat"` |
| `department_raw` | TEXT | NULL | название подразделения как на странице программы |
| `department_id` | TEXT, каноническое значение из `/sveden/struct` (см. §6.2) — **НЕ** foreign key: в схеме этой фичи нет отдельной таблицы `departments`, это просто резолвнутая строка/код, переносимая как есть | NULL до резолва | NULL, если резолв не удался — не блокирует запись, просто `identity_key` будет менее точным для этого сотрудника |
| `disciplines` | TEXT (JSON-массив) | NULL | union всех `teachingDiscipline` схлопнутых программ (FR-003) |
| `gen_experience` | INTEGER | NULL | лет, `itemprop="genExperience"` |
| `spec_experience` | INTEGER | NULL | лет, `itemprop="specExperience"` |
| `teaching_level` | TEXT | NULL | `itemprop="teachingLevel"` |
| `employee_qualification` | TEXT | NULL | `itemprop="employeeQualification"` |
| `prof_development` | TEXT | NULL | `itemprop="profDevelopment"` |
| `teaching_op` | TEXT | NULL | `itemprop="teachingOp"` |
| `identity_key` | TEXT | NOT NULL | `fio_normalized + university_id + department_id + genExperience/specExperience`, вычисляется при записи (§4.1.1) |
| `source_url` | TEXT | NOT NULL | страница конкретной программы, где нашли запись |

**Валидация**: `fio_normalized` не пустая строка; `university_id` существует в `universities`.
**Дедуп**: уникальность по (`university_id`, `identity_key`) — вставка второй строки с тем же
ключом мержит `disciplines` (union), не создаёт вторую запись (FR-003).

## `vak_raw`

Сырые объявления/защиты из ВАК API (см. `research.md`, §5 — маппинг полей 1:1 с JSON-ответом).

| Поле | Тип | Обязательность | Комментарий |
|---|---|---|---|
| `vak_id` | TEXT PK | — | `id` (uuid) из API |
| `old_id` | TEXT | NULL | для ссылок на автореферат |
| `fio` | TEXT | NOT NULL | |
| `fio_normalized` | TEXT | NOT NULL | та же нормализация, что и у `employees_raw.fio_normalized` |
| `dissertation_type` | TEXT | NOT NULL | «Кандидатская»/«Докторская» |
| `specialty` | TEXT | NULL | как в API, строка «код - название» |
| `branch` | TEXT | NULL | «отрасль науки» текстом |
| `topic` | TEXT | NULL | `dissertation_name` из API |
| `defend_org` | TEXT | NOT NULL | организация защиты, как в API |
| `council_cipher` | TEXT | NULL | шифр диссовета (detail API) |
| `org_address` | TEXT | NULL | адрес организации защиты (detail) |
| `org_phone` | TEXT | NULL | телефон организации (detail) |
| `date_defend` | TEXT (ISO date) | NULL | |
| `is_pilot_branch` | BOOLEAN | NOT NULL | какой веткой запроса получена запись (`is_pilot=true/false`) — не путать с `universities.is_pilot` |

**Валидация**: `fio` не пустая; `is_pilot_branch` заполняется пайплайном (не полем ответа API).

## `candidates`

Единая карточка — результат матчера (§4.2 `candidate-pipeline-architecture.md`, FR-006/FR-007).

| Поле | Тип | Обязательность | Комментарий |
|---|---|---|---|
| `candidate_id` | **TEXT PK**, формат `c_<zero-padded-number>` (напр. `c_00123`) | — | строковый, а не числовой формат — чтобы совпадать с `"candidate_id": "c_00123"` из `contracts/future-layer2-vk-stub-contract.md` и первоисточника `candidate-pipeline-architecture.md`, §6.6 (единый формат по всем документам фичи) |
| `full_name` | TEXT | NOT NULL | |
| `identity_key` | TEXT | NULL | только для карточек с вузом (`site_and_vak`/`site_and_vak_probable`/`site_no_vak`); NULL у `vak_no_site` |
| `match_status` | TEXT | NOT NULL, CHECK IN (4 значения) | `site_and_vak` / `site_and_vak_probable` / `vak_no_site` / `site_no_vak` — закрытый список, БЕЗ 5-го значения (FR-006) |
| `needs_review` | BOOLEAN | NOT NULL, DEFAULT false | `true`, только если есть строка в `possible_namesakes` (FR-007) — вычисляется, не задаётся вручную |
| `university_id` | FK → universities | NULL у `vak_no_site` | |
| `department_id` | TEXT, каноническое значение из `/sveden/struct` | NULL у `vak_no_site` | НЕ foreign key — нет отдельной таблицы `departments`, см. примечание у `employees_raw.department_id` ниже |
| `post` | TEXT | NULL | слой 1, `itemprop="post"` |
| `degree` | TEXT | NULL | |
| `academic_title` | TEXT | NULL | слой 1, `itemprop="academStat"` |
| `disciplines` | TEXT (JSON-массив) | NULL | пусто у `vak_no_site` |
| `gen_experience` | INTEGER | NULL | слой 1; часто пуст (см. `university-sites-analysis.md`) |
| `spec_experience` | INTEGER | NULL | слой 1; заполнен чаще |
| `source_url` | TEXT | NULL | program page слоя 1 |
| `defenses` | TEXT (JSON-массив объектов) | NULL | `{date, specialty, specialty_code, specialty_name, branch, dissertation_type, topic, defend_org, council_cipher, org_address, org_phone, is_pilot}`; пусто у `site_no_vak` |
| `email`, `phone`, `contact_type`, `contact_source_url` | TEXT | NULL, всегда NULL в этой фиче | зарезервированы под будущий layer2 — колонки существуют в xlsx (FR-012), но эта фича их не заполняет |
| `vk_url`, `vk_score`, `vk_status` | TEXT/REAL | NULL, всегда NULL в этой фиче | то же для будущего VK |
| `candidate_content_hash` | TEXT | NOT NULL | хэш исходных полей карточки — под будущую инкрементальность (FR-017), не используется для skip-логики в этой фиче |
| `first_seen_run_id`, `last_seen_run_id` | FK → runs | NOT NULL | |

**Состояния**: карточка не меняет `match_status` после создания в рамках одного `run` (статус
присваивается один раз при матче); между прогонами матчер пересчитывает статус заново по тем же
правилам (Принцип V — нет версионирования решений между прогонами).

## `possible_namesakes`

Связь «вероятный тёзка» — НЕ статус карточки (§4.2.1). Наличие строки здесь — единственная причина,
по которой `candidates.needs_review = true`.

| Поле | Тип | Обязательность | Комментарий |
|---|---|---|---|
| `id` | INTEGER PK | — | |
| `site_candidate_id` | FK → candidates | NOT NULL | карточка со статусом `site_no_vak` |
| `vak_candidate_id` | FK → candidates | NOT NULL | карточка со статусом `vak_no_site` |
| `reason` | TEXT | NOT NULL | короткое пояснение противоречия |

## `runs` / `run_steps`

Метаданные прогона — идемпотентность и чекпоинты (FR-008), не бизнес-данные.

**`runs`**: `run_id` PK, `started_at`, `finished_at` (NULL пока не завершён), `status`
(`running`/`success`/`failed`), `is_full` (BOOLEAN — форсирован ли `--full`).

**`run_steps`**: `run_id` FK, `step` (`layer1`/`vak`/`match`/`export`), `university_id` FK (NULL для
шагов не по вузу, напр. `vak`/`match`/`export`), `status` (`pending`/`done`/`error`),
`university_site_hash` (только для `step=layer1`, по вузу — под будущую инкрементальность, FR-017),
`checkpoint_cursor` (для `step=vak` — номер последней успешно обработанной страницы API).

**Резюме прогона**: `run` считается продолжаемым, если есть `run_id` со `status=running` без
`finished_at` — на перезапуске пайплайн ищет такой `run` и продолжает с первого `run_step` со
`status != done` для соответствующего шага, вместо создания нового `run_id` (FR-008).
