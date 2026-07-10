---

description: "Task list for Core Pipeline Foundation (Layer 1 + VAK + Matcher, no Layer 2 / VK)"
---

# Tasks: Core Pipeline Foundation (Layer 1 + VAK + Matcher, no Layer 2 / VK)

**Input**: Design documents from `specs/001-core-pipeline-mvp/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/, research.md, quickstart.md (all present)

**Tests**: `plan.md` (Technical Context → Testing, per `research.md` §1) explicitly commits to
`pytest` unit tests on pure logic only (normalization, `identity_key`, matcher, xlsx contract
shape) — no network-hitting contract/integration tests. Task list below includes exactly those
unit tests, alongside implementation, not as a blocking TDD gate.

**Organization**: Tasks are grouped by user story (spec.md: US1 P1, US2 P2, US3 P3) to enable
independent implementation and testing of each story.

**Remediation note**: task IDs T006 onward were renumbered during `/speckit-clarify` to insert
T006 (new, closes `/speckit-analyze` finding I1 — FR-015 had zero task coverage). If you have an
older copy of this file with different IDs, discard it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Paths below are relative to repo root, matching `plan.md` → Project Structure

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton and dependency manifest — nothing here is business logic.

- [x] T001 Create package skeleton per `plan.md` → Project Structure: `app/__init__.py`,
  `app/sources/vak/__init__.py`, `app/sources/universities/__init__.py`,
  `app/matching/__init__.py`, `app/vk/__init__.py`, `app/export/__init__.py`, `app/db/__init__.py`,
  plus empty `data/raw/.gitkeep`, `output/.gitkeep`, `logs/.gitkeep`, `tests/unit/__init__.py`
- [x] T002 [P] Create dependency manifest `pyproject.toml` listing `httpx`, `lxml`, `selectolax`,
  `openpyxl`, `PyYAML`, `python-dotenv`, `rapidfuzz`, `pytest` per `plan.md` → Technical Context
- [x] T003 [P] Create `config.yaml` at repo root with `run: {layer1: true, vak: true, match: true,
  layer2: false, vk: false}` and `limits: {request_delay_sec: 1.5, max_universities: null}` per
  `contracts/cli-contract.md`
- [x] T004 [P] Create `.env.example` at repo root with a `VK_TOKEN=` placeholder (unused this
  feature, reserved per `constitution.md` → Technology Constraints)
- [x] T005 [P] Update `.gitignore` for `data/state.sqlite`, `data/backups/`, `data/raw/`,
  `output/`, `logs/`
- [x] T006 [P] Create empty scaffold modules `app/sources/universities/layer2.py` and
  `app/vk/__init__.py` with docstrings describing their future input/output contract per
  `contracts/future-layer2-vk-stub-contract.md` (FR-015). No crawling/AI-parsing/VK-API logic, no
  import from `app/cli.py` — this task only satisfies the "scaffold exists" half of FR-015; the
  "not wired into any command" half is verified by the absence of any such wiring in Phases 2-5
  below

**Checkpoint**: Repo has an importable `app` package and runnable config — no logic yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared code that every user story needs — DB schema, config/CLI skeleton, shared
HTTP client, shared normalization.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T007 Write SQLite DDL in `app/db/schema.sql` for `universities`, `employees_raw`, `vak_raw`,
  `candidates`, `possible_namesakes`, `runs`, `run_steps` per `data-model.md` (deliberately no
  `contacts`/`vk_hits` tables — see `data-model.md` intro note). `candidates.candidate_id` is
  `TEXT` (format `c_<zero-padded-number>`), not `INTEGER` — see `data-model.md` for rationale
- [x] T008 Implement `app/db/repository.py`: open/init `data/state.sqlite` from `schema.sql`,
  generic upsert helpers, run/run_step checkpoint helpers (`create_run`, `mark_step_done`,
  `mark_step_error`, `find_resumable_run`) per `data-model.md` → `runs`/`run_steps`, and a single
  shared `backup_state(reason: str)` helper (copies `data/state.sqlite` to
  `data/backups/state_<run_id>.sqlite`, rotates to keep only the last N) — reused by both the
  `reset` and post-`run` backup call sites (T038/T039) instead of duplicating the logic
- [x] T009 [P] Implement `app/config.py`: load `config.yaml` + `.env`; if `run.layer2` or `run.vk`
  is `true`, raise a clear "step not implemented" error immediately (FR-016)
- [x] T010 [P] Implement `app/matching/normalize.py`: FIO normalization (case, `ё→е`, whitespace,
  hyphen spacing in double surnames) and organization-name normalization — shared by layer1 (US1)
  and VAK (US2) per `candidate-pipeline-architecture.md` §4.1
- [x] T011 [P] Implement `app/sources/http_client.py`: shared retry/backoff wrapper (up to 3
  attempts, 1s→2s→4s backoff on network errors/5xx, no retry on 4xx) per FR-009 / Principle VI
- [x] T012 Implement `app/cli.py` skeleton: `argparse` with subcommands `run`, `step
  {layer1,vak,match}`, `export`, `status`, `reset`, wired to placeholder step functions and to the
  T009 config loader (including its FR-016 fail-fast check at the top of `run`)
- [x] T013 [P] Implement `app/registry/loader.py`: read `data/university_registry.csv` into the
  `universities` table (`official_name`, `aliases`, `domain`, `region`, `accreditation_status`,
  `vk_group_id`, `is_pilot`)

**Checkpoint**: Foundation ready — user story implementation can now begin.

---

## Phase 3: User Story 1 - Собрать сотрудников вузов из слоя 1 (Priority: P1) 🎯 MVP

**Goal**: Обойти `/sveden/employees` вузов реестра, распарсить `itemprop`-микроразметку, схлопнуть
дубли внутри вуза, зафиксировать недоступные вузы, резюмировать прерванный обход.

**Independent Test**: `quickstart.md` → Сценарий 1 (`python -m app step layer1` на 5 вузах, один
из которых недоступен; проверка чекпоинта через Ctrl+C и перезапуск).

### Tests for User Story 1

- [x] T014 [P] [US1] Unit tests for FIO/organization normalization in
  `tests/unit/test_normalize.py` (case, `ё→е`, whitespace, hyphen cases)
- [x] T015 [P] [US1] Unit tests for `identity_key` construction, cross-run ±1–2 year experience
  tolerance, and disciplines-union dedup behavior in `tests/unit/test_identity_key.py`

### Implementation for User Story 1

- [x] T016 [P] [US1] Implement `app/sources/universities/struct.py`: fetch `/sveden/struct`,
  extract subdivision names, fuzzy-match (`rapidfuzz`) against raw department names to resolve a
  canonical `department_id`, cache the result **for the duration of the current run** (in-memory,
  keyed by `university_id` + raw department string) so the same fuzzy comparison is not repeated
  twice within one run — per `research.md` §3 and FR-002. Cross-run persistence of this cache is
  explicitly out of scope for this feature (see spec.md FR-002 clarification); the next run
  re-resolves from `/sveden/struct` again, which is safe because results are deterministic inputs
  to `identity_key`, not a source of drift
- [x] T017 [P] [US1] Implement `app/matching/identity_key.py`: build `identity_key =
  fio_normalized + university_id + department_id + genExperience/specExperience` per FR-003 /
  `candidate-pipeline-architecture.md` §4.1.1
- [x] T018 [US1] Implement `app/sources/universities/layer1.py`: fetch the `/sveden/employees`
  index page, collect program page links, fetch each program page, parse `itemprop` microdata
  (`fio`, `post`, `degree`, `academStat`, `teachingDiscipline`, `genExperience`,
  `specExperience`) into raw employee records (FR-001, `research.md` §4)
- [x] T019 [US1] In `app/sources/universities/layer1.py`, dedup raw records per university by
  `identity_key` (T017), union `disciplines` across duplicate program listings without loss or
  duplication, and upsert into `employees_raw` via `app/db/repository.py` (FR-003)
- [x] T020 [US1] In `app/sources/universities/layer1.py`, wire per-request retry/backoff (T011);
  on persistent failure (repeated 5xx/timeout/unexpected structure) record `universities.
  layer1_status` and a `university_errors` entry instead of aborting the whole run (FR-009,
  FR-013, spec.md US1 Acceptance Scenario 2)
- [x] T021 [US1] Add per-university checkpointing for the `layer1` step in `run_steps` (T008) so
  an interrupted `run` resumes at the next unprocessed university instead of restarting (FR-008,
  spec.md US1 Acceptance Scenario 3). In the same checkpoint write, compute a hash of the raw
  `/sveden/employees` content pulled for that university and store it as
  `run_steps.university_site_hash` (FR-017) — this task is the only place that field gets written
- [x] T022 [US1] Wire `app step layer1` and the `layer1` stage of `app run` in `app/cli.py` to
  T018–T021

**Checkpoint**: User Story 1 is independently functional and testable via `quickstart.md` Scenario 1.

---

## Phase 4: User Story 2 - Обогатить карточки данными ВАК и определить статус совпадения (Priority: P2)

**Goal**: Выгрузить объявления ВАК (обе `is_pilot`-ветки при необходимости) и смержить их со
слоем 1 в единую карточку с одним из 4 статусов, без потери карточек и без 5-го статуса.

**Independent Test**: `quickstart.md` → Сценарий 2 (`step vak` + `step match`; проверка
распределения `match_status` и содержимого `possible_namesakes`).

### Tests for User Story 2

- [x] T023 [P] [US2] Unit tests for matcher status assignment in `tests/unit/test_matcher.py`:
  all 4 statuses, the `is_pilot` both-branches case, and the conflicting-signal case (FR-006,
  FR-007, spec.md US2 Acceptance Scenarios 1-4). For the conflicting-signal case, assert **both**
  that a `possible_namesakes` row is created with `needs_review=true` on both cards **and** that
  both cards keep every layer1/VAK field they had before (full_name, university/department for
  the site card; `defenses[]` for the VAK card) — i.e. explicitly cover spec.md SC-004, not just
  the status/flag values
- [x] T024 [P] [US2] Implement `app/sources/vak/client.py`: paginated `GET
  /api/att/adverts/?page=N&pageSize=100&is_pilot={true,false}` against `https://vak.gisnauka.ru`
  via the shared client (T011), 8–10s timeout (`research.md` §5)
- [x] T025 [US2] Implement `app/sources/vak/parser.py`: map API JSON fields (`fio`,
  `dissertation_type`, `specialty`, `branch`, `dissertation_name`→`topic`, `defend_org`,
  `date_defend`, `id`/`old_id`) into `vak_raw` rows, tagging `is_pilot_branch` from the query used
  (`data-model.md` → `vak_raw`)
- [x] T026 [US2] In `app/sources/vak/client.py`, for universities with `is_pilot=true` in
  `universities`, fetch and store records from **both** the `is_pilot=false` and `is_pilot=true`
  branches (FR-004)
- [x] T027 [US2] Add per-page checkpointing (`checkpoint_cursor` in `run_steps`) for the `vak`
  step so an interrupted VAK pull resumes from the last completed page (FR-008)
- [x] T028 [US2] Implement `app/matching/matcher.py`: normalize (T010) FIO/organization, fuzzy-
  match `defend_org` against `universities.official_name`/`aliases` (`rapidfuzz`), match by exact
  normalized FIO, and assign exactly one of the 4 `match_status` values per §4.2 rules (FR-006)
- [x] T029 [US2] In `app/matching/matcher.py`, detect FIO-matched-but-conflicting-signal pairs
  (lower site degree than the VAK record implies, or unrelated specialty/discipline); write a
  `possible_namesakes` row and set `needs_review=true` on both cards instead of introducing a 5th
  status, **without removing or blanking out either card's existing fields** (FR-007, §4.2.1,
  spec.md SC-004)
- [x] T030 [US2] In `app/matching/matcher.py`, build unified `candidates` rows (`candidate_id` in
  the `c_<zero-padded-number>` format per `data-model.md`, `full_name`, `identity_key`,
  `match_status`, `needs_review`, university/department/degree/disciplines, `defenses[]`,
  `candidate_content_hash`) and upsert via `app/db/repository.py` (`data-model.md` →
  `candidates`, FR-017 for the hash)
- [x] T031 [US2] Wire `app step vak`, `app step match`, and their stages of `app run` in
  `app/cli.py` to T024–T030

**Checkpoint**: User Stories 1 AND 2 both work independently, per `quickstart.md` Scenarios 1-2.

---

## Phase 5: User Story 3 - Один прогон без слоя 2 и VK (Priority: P3)

**Goal**: Связать US1+US2 в единый `run`, экспортировать xlsx-контракт целиком (с пустыми
layer2/VK колонками), и жёстко застраховаться от преждевременного включения layer2/VK.

**Independent Test**: `quickstart.md` → Сценарии 3 и 4 (полный `run`, повторный `export` без
сети, и отказ при `layer2: true`/`vk: true`).

### Tests for User Story 3

- [x] T032 [P] [US3] Unit test asserting the xlsx `candidates` sheet always includes
  `email`/`phone`/`vk_url`/`vk_score`/`vk_status` columns, present but empty, in
  `tests/unit/test_xlsx_contract.py` per `contracts/xlsx-contract.md` (FR-012, SC-005)

### Implementation for User Story 3

- [x] T033 [US3] Implement the `candidates` sheet in `app/export/xlsx.py` per
  `contracts/xlsx-contract.md` (flattening `defenses[]` to the latest-by-date defense per row;
  layer2/VK columns always present, always empty in this feature)
- [x] T034 [US3] Implement the `possible_namesakes`, `university_errors`, and `run_meta` sheets in
  `app/export/xlsx.py` per `contracts/xlsx-contract.md`
- [x] T035 [US3] Wire `app export` in `app/cli.py` to read purely from `data/state.sqlite` (T033,
  T034) with zero network calls (SC-007)
- [x] T036 [US3] Implement `app run` orchestration in `app/cli.py`: sequential `layer1 → vak →
  match → export`, resuming an in-progress `run` via `runs`/`run_steps` (T008) unless `--full` is
  passed, with the FR-016 fail-fast check (T009/T012) actually blocking before any step starts
- [x] T037 [US3] Implement `app status` in `app/cli.py`: print university counts by
  `layer1_status`, row counts for `employees_raw`/`vak_raw`/`candidates`,
  `data/state.sqlite` size, and whether the last run finished or was interrupted
- [x] T038 [US3] Implement `app reset` in `app/cli.py`: call the shared `backup_state("reset")`
  helper (T008), then clear state (FR-014)
- [ ] T039 [US3] After a successful `run` in `app/cli.py`, call the same shared
  `backup_state("post_run")` helper (T008) so both backup triggers share one rotation
  implementation (FR-014)

**Checkpoint**: All three user stories work independently — full M0+M1 milestone complete
(`app-architecture.md` §8), without layer2/VK.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T040 [P] Run `quickstart.md` end-to-end against a small (~5 university) slice of
  `data/university_registry.csv` and record actual results against expected outcomes
- [ ] T041 [P] Document `python -m app run/step/export/status/reset` usage in a short
  `README.md`, mirroring `contracts/cli-contract.md`
- [ ] T042 Review FR-001…FR-017 against the implementation and close any gaps found during T040

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends only on Foundational
- **User Story 2 (Phase 4)**: Depends on Foundational; consumes `employees_raw` populated by US1
  for matching, so in practice runs after US1, though its own tests/client code (T023, T024) can
  start as soon as Foundational is done
- **User Story 3 (Phase 5)**: Depends on Foundational + needs `candidates` rows from US2 to
  produce a meaningful export, so runs after US1+US2
- **Polish (Phase 6)**: Depends on all three user stories being complete

### User Story Dependencies

- **US1 (P1)**: No dependency on other stories — first, independently testable slice
- **US2 (P2)**: Functionally needs US1's `employees_raw` to have anything to match against
  (`vak_no_site`-only candidates are still producible without US1, but the story's own acceptance
  scenarios 1/3 need site records) — build after US1
- **US3 (P3)**: Needs US1 (`employees_raw`) and US2 (`candidates`, `match_status`) to have real
  data to export/orchestrate — build after US1 and US2

### Within Each User Story

- Tests (T014/T015, T023, T032) can be written alongside implementation, not strictly before
- Parsers/clients before matcher/export logic that consumes their output
- CLI wiring task is last in each story (needs the story's other pieces to exist)

### Parallel Opportunities

- All Setup tasks marked [P] (T002-T006) can run in parallel once T001 creates the skeleton
- Foundational [P] tasks (T009, T010, T011, T013) touch different files and can run in parallel
  once T007/T008 (schema + repository) exist
- Within US1: T014/T015 (tests) and T016/T017 (struct.py / identity_key.py) can run in parallel;
  T018-T022 are sequential (same file, layer1.py, plus shared cli.py)
- Within US2: T023 (test) and T024 (vak client) can run in parallel; T025-T031 are largely
  sequential (client → parser → matcher → cli)
- Within US3: T032 (test) can run in parallel with T033; T033/T034 share `xlsx.py` so are
  sequential with each other
- T040/T041 in Polish can run in parallel

---

## Parallel Example: Foundational phase

```bash
# After T007 (schema) and T008 (repository) are done, run together:
Task: "Implement app/config.py per FR-016"
Task: "Implement app/matching/normalize.py per §4.1"
Task: "Implement app/sources/http_client.py per FR-009"
Task: "Implement app/registry/loader.py"
```

## Parallel Example: User Story 1

```bash
Task: "Unit tests for normalization in tests/unit/test_normalize.py"
Task: "Unit tests for identity_key in tests/unit/test_identity_key.py"
Task: "Implement app/sources/universities/struct.py (department_id resolution)"
Task: "Implement app/matching/identity_key.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (blocks everything else)
3. Complete Phase 3: User Story 1 — this alone already produces a real, checkable artifact:
   deduplicated employee records per university, with `university_errors` and resumability
4. **STOP and VALIDATE**: run `quickstart.md` Scenario 1 against a small slice of the registry

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. Add US1 → validate independently (list of employees per university, no VAK/matcher yet)
3. Add US2 → validate independently (4 statuses, `possible_namesakes` populated)
4. Add US3 → validate independently (single `run` command, full xlsx, FR-016 guard) — this is the
   actual deliverable of the branch (`спека`, US3): "прогнать без слоя 2 и VK"
5. Each story adds value without requiring layer2/VK, which remain untouched scaffolds

### Solo-Developer Note

Given this project currently has one maintainer (see `constitution.md` → Governance), "parallel
team strategy" doesn't apply — the Parallel Opportunities above are about which tasks are safe to
batch together in one sitting (different files, no ordering dependency), not about multiple
developers.

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Commit after each task or logical group
- Stop at any checkpoint (end of Phase 3, 4, or 5) to validate that story independently via
  `quickstart.md` before moving on
- Avoid: vague tasks, same-file conflicts marked [P], cross-story dependencies that break
  independent testability
- `app/sources/universities/layer2.py` and `app/vk/__init__.py` **are** created in this task list
  (T006), per FR-015 — but only as empty scaffolds with a contract docstring. No task in Phases
  2-5 implements their logic or calls them from `app/cli.py`; keep it that way until a future
  feature explicitly picks up layer2/VK
