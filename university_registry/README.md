# university_registry — сборка списка вузов, доменов и VK-групп

Всё в этой папке решает одну задачу: собрать `data/university_registry.csv` — список аккредитованных вузов + домен сайта + `vk_group_id`. Больше эта папка ничего не делает и не должна — если появится скрипт не про реестр вузов, ему место в другой папке, не здесь.

This registry is the input `university_registry` referenced throughout `research/pipeline/candidate-pipeline-architecture.md` (§3) and `research/pipeline/app-architecture.md` (§5) — same columns, same file, just built ahead of time here since the actual `app/` package doesn't exist yet.

## Prerequisites

- Python 3.12+
- `playwright` (+ `python -m playwright install chromium`) for `resolve_vk_browser.py` only
- Everything else — stdlib only

## Data layout

```text
data/
  university_registry.csv          # main artifact — tracked in git
  university_registry.json         # same data, JSON — regenerated, not in git
  university_registry.meta.json    # counts/stats about the last build — regenerated, not in git
  university_registry_vk_review.csv # rows resolve_vk_browser.py couldn't resolve — regenerated, not in git
  raw/                              # downloads & exploration scratch — gitignored wholesale, rebuild via scripts below
    accredreestr.zip                 # source ZIP from ISGA (~350 MB), via download_accred_data.py
    xml_sample.txt                   # first ~200 lines of the ZIP's XML, via _probe_xml.py (debug only)
    raoo_page.html, raoo_map_points.json  # cached obrnadzor RAOO page + extracted map points
    isga_app.js, isga_opendata.html, islod.html, islod_opendata.html  # dead-end exploration
      # (JS-SPA shells with no data inside — kept only as a record of what was tried
      #  before landing on the ISGA `filezip` API used by download_accred_data.py)
    ddg_test.html                    # leftover from a DuckDuckGo search probe, not used by any script
```

Only `university_registry.csv` is meant to be committed — everything else here is regeneratable or dead-end scratch (see `.gitignore`).

## Steps

### 1. Download official accred data (once)

```bash
python university_registry/download_accred_data.py
```

Saves `data/raw/accredreestr.zip` (~350 MB) from Rosobrnadzor ISGA.

### 2. Build registry CSV

```bash
python university_registry/build_university_registry.py
```

Reads the ZIP, filters **действующие** orgs with **высшим/послевузовским** образованием (head orgs, not branches), deduplicates by OGRN.

Outputs:

- `data/university_registry.csv` — main artifact for the app
- `data/university_registry.json` — same data, JSON
- `data/university_registry.meta.json` — counts/stats

### 3. Resolve VK groups (browser, без токена)

```bash
python university_registry/resolve_vk_browser.py --only-missing
python university_registry/resolve_vk_browser.py --only-missing --limit 20   # пробный прогон
```

Ищет VK так:
1. Ссылка `vk.com/...` на сайте вуза (`/` и `/sveden/common`)
2. Угадывание по домену (`urfu.ru` → `vk.com/urfu`)
3. Yandex `site:vk.com {alias} университет` в headless Chromium

Спорные/не найденные → `data/university_registry_vk_review.csv`

Альтернатива с VK API (если есть токен): `university_registry/resolve_vk_groups.py`

### Helpers (debug / one-off, не часть основного пайплайна)

- `university_registry/parse_raoo_points.py` — extract 1000 map points from cached `data/raw/raoo_page.html` → `data/raw/raoo_map_points.json`
- `university_registry/extract_urls.py` — list data URLs on obrnadzor open-data page from cached `data/raw/raoo_page.html`
- `university_registry/_probe_xml.py` — dump первые ~200 строк XML из `data/raw/accredreestr.zip` в `data/raw/xml_sample.txt`, чтобы посмотреть структуру без распаковки всего архива. Имя XML-файла внутри ZIP захардкожено на дату скачивания — если `accredreestr.zip` перекачали заново и имя файла внутри изменилось, поправь константу в начале скрипта (сам `build_university_registry.py` от этого не зависит — он находит XML в архиве автоматически)

## CSV columns

| Column | Description |
|---|---|
| `id` | Stable id (`uni_0001`, ...) |
| `official_name` | Full name from registry |
| `aliases` | Pipe-separated short names |
| `domain` | Website hostname (no scheme) |
| `region` | Subject of RF |
| `inn`, `ogrn` | Legal ids |
| `accreditation_status` | Usually `Действующее` |
| `vk_group_id` | VK community id for MVP search |
| `vk_screen_name`, `vk_url` | Human-readable VK link |
| `is_pilot` | `true` if likely has independent degree awarding (VAK is_pilot) |
| `source_notes` | Provenance / resolver notes |
