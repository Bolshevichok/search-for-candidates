# Contract: xlsx-экспорт

Единственный артефакт, который видит заказчик/оператор (`app-architecture.md`, §7).

## Лист `site_employees`

Сотрудники с сайтов вузов: `site_no_vak`, `site_and_vak`, `site_and_vak_probable`.

| Колонка | Заполняется в MVP? |
|---|---|
| `full_name`, `match_status`, `needs_review` | да |
| `university`, `department` | да |
| `post`, `degree`, `academic_title` | да (layer1) |
| `disciplines` | да (`; `) |
| `gen_experience`, `spec_experience` | да (layer1, `itemprop`) |
| `email`, `phone`, `contact_url` | нет (layer2 не реализован) |
| `vk_url`, `vk_score`, `vk_status` | нет (VK не реализован) |
| `source_url` | да |

## Лист `vak_candidates`

Записи ВАК: `vak_no_site`, `site_and_vak`, `site_and_vak_probable`.

| Колонка | Заполняется в MVP? |
|---|---|
| `full_name`, `match_status`, `needs_review` | да |
| `branch`, `specialty_code`, `specialty_name` | да (detail API) |
| `dissertation_type`, `topic`, `defense_date` | да |
| `defend_org`, `council_cipher`, `org_address`, `org_phone` | да |
| `is_pilot` | да |

Слияния попадают на **оба** листа с соответствующими колонками.

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
| `candidates_total`, счётчики по `match_status` | |
| `is_full_run` | `true`/`false` |
