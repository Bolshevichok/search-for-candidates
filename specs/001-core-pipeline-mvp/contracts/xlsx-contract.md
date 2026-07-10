# Contract: xlsx-экспорт

Единственный артефакт, который видит заказчик/оператор (`app-architecture.md`, §1, §7). В рамках
этой фичи содержимое — только то, что дают layer1 + VAK + матчер; колонки layer2/VK присутствуют,
но всегда пустые (FR-012, SC-005) — это осознанно, чтобы будущее включение этих шагов не требовало
менять структуру файла.

## Лист `candidates`

Одна строка = одна карточка кандидата (`candidates` из `data-model.md`).

| Колонка | Тип | Заполняется в этой фиче? |
|---|---|---|
| `full_name` | текст | да |
| `match_status` | одно из 4: `site_and_vak` / `site_and_vak_probable` / `vak_no_site` / `site_no_vak` | да |
| `needs_review` | `true`/`false` | да (по `possible_namesakes`) |
| `university` | текст | да, пусто у `vak_no_site` |
| `department` | текст | да, пусто у `vak_no_site` |
| `degree` | текст | да |
| `disciplines` | текст (список через `; `) | да, пусто у `vak_no_site` |
| `defense_date` | дата | да, из `defenses[0]` (первая/последняя защита — см. Note ниже), пусто у `site_no_vak` |
| `dissertation_type` | текст | да |
| `specialty` | текст, «код - название» как в API ВАК | да |
| `branch` | текст | да |
| `topic` | текст | да |
| `defend_org` | текст | да |
| `is_pilot` | `true`/`false` | да |
| `email`, `phone`, `contact_type`, `contact_url` | — | **нет, всегда пусто** (layer2 не реализован) |
| `vk_url`, `vk_score`, `vk_status` | — | **нет, всегда пусто** (VK не реализован) |
| `source_notes` | текст | да, краткое значение `match_status` |

**Note про несколько защит**: если у кандидата несколько записей `defenses[]` (кандидатская, потом
докторская), в строку листа `candidates` попадает самая **последняя по дате** защита;
полный список защит — не теряется, он есть в `data/state.sqlite` (`candidates.defenses` JSON), но
на xlsx-лист в этой фиче не разворачивается в отдельные строки (не запрошено задачей — можно
добавить отдельной фичей, если понадобится «одна строка = одна защита»).

## Лист `possible_namesakes`

| Колонка | Комментарий |
|---|---|
| `site_full_name`, `site_university` | из карточки `site_no_vak` |
| `vak_full_name`, `vak_defend_org` | из карточки `vak_no_site` |
| `reason` | как в `possible_namesakes.reason` |

## Лист `university_errors`

| Колонка | Комментарий |
|---|---|
| `university`, `domain` | из `universities` |
| `error_type` | `unreachable` / `timeout` / `unexpected_structure` / `unresolved_domain` |
| `last_attempt_at` | |

## Лист `run_meta`

| Колонка | Комментарий |
|---|---|
| `run_id`, `started_at`, `finished_at` | |
| `universities_ok`, `universities_error` | счётчики |
| `candidates_total` | по каждому из 4 `match_status` — отдельные счётчики колонками |
| `is_full_run` | `true`/`false` |
