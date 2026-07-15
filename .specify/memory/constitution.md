<!--
Sync Impact Report (Amendment)
- Version change: 1.3.1 -> 1.4.0 (MINOR -- a new mandatory rule for adopting simpler
  existing solutions was added)
- Modified principles: I. Simplicity First -- before writing a custom implementation,
  developers MUST consider the standard library, an existing project dependency, or a mature
  focused dependency; when it meets the requirement and produces less code, it MUST be used
- Added/removed sections: none
- Templates checked: ✅ .specify/templates/plan-template.md and
  .specify/templates/tasks-template.md updated to require manual verification and record the
  simpler existing alternative; ✅ .specify/templates/spec-template.md remains compatible
- Runtime guidance: ✅ README.md updated to remove the obsolete VK reference
- Follow-up TODOs: none
-->

<!--
Sync Impact Report
- Version change: (template) → 1.0.0
- Ratification: first constitution for this project — no prior version existed
- Principles added: I. Simplicity First (NON-NEGOTIABLE), II. Batch CLI Not a Service,
  III. Idempotent Checkpointed Runs (NON-NEGOTIABLE), IV. Sites Are the Source of Truth /
  VAK Is Additive, V. Mandatory Enrichment No Feedback Loop, VI. One Retry Policy
- Sections added: Technology Constraints, Development Workflow, Governance
  (filled in from template placeholders — no prior content removed)
- Templates checked:
  ✅ .specify/templates/plan-template.md — Constitution Check gate is generic/dynamic
     (`[Gates determined based on constitution file]`), no structural edit needed
  ✅ .specify/templates/spec-template.md — no mandatory section conflicts with principles above
  ✅ .specify/templates/tasks-template.md — generic phase structure is compatible as-is
  ⚠ .specify/templates/commands/*.md — N/A, directory does not exist (this project uses the
     skills-mode `cursor-agent` integration, not command-file mode)
  ⚠ Runtime guidance docs (README.md, AGENTS.md, docs/quickstart.md) — N/A, none exist yet
    at the repo root; create them referencing this constitution if/when they are added
- Deferred: no testing-strategy principle included — not decided yet for this project;
  add as a MINOR amendment if/when a testing approach is agreed

Sync Impact Report (Amendment)
- Version change: 1.0.0 → 1.0.1 (PATCH — clarification, no principle removed/redefined)
- Modified principles: V. Mandatory Enrichment, No Feedback Loop — added an explicit scoping
  clause: the "run automatically on every full run" language applies once later enrichment is
  implemented and describes a full (M3) run; earlier milestones (M0–M2) shipping without
  later enrichment are explicitly not a violation. Raised during `/speckit-analyze` on
  `specs/001-core-pipeline-mvp/` (finding C1): `plan.md`'s Constitution Check was reinterpreting
  this principle inline to justify feature 001 instead of the constitution stating the scope
  explicitly — this amendment removes the need for that inline reinterpretation.
- Added/removed sections: none
- Templates requiring updates: ✅ `specs/001-core-pipeline-mvp/plan.md` Constitution Check row
  for Principle V updated to cite the amended text directly (no other templates reference this
  principle by name)

Sync Impact Report (Amendment)
- Version change: 1.0.1 → 1.1.0 (MINOR — concrete simplicity rules and migration scope added)
- Modified principles: I. Simplicity First — defined readable, direct code; prohibited speculative
  layers, routine logging/tracing/telemetry, and comments that merely restate the code; clarified
  that correctness, retries, checkpointing, useful errors, and focused tests remain mandatory
- Modified sections: Development Workflow — new rules apply to feature 002 and later work;
  pre-existing code and work already under way on Layer 2 are migrated only through a separate
  approved feature or when materially changed after merge
- Templates checked: ✅ plan/spec/tasks templates remain compatible; no template edit required

Sync Impact Report (Amendment)
- Version change: 1.1.0 → 1.2.0 (MINOR — explicit project-wide verification policy added)
- Modified principles: I. Simplicity First — automated unit, integration, contract, end-to-end and
  other test suites are not created; manual run verification is the required project practice
- Modified sections: Development Workflow — manual run and result review are now the completion gate
- Templates requiring updates: ✅ plan/spec/tasks templates already allow manual verification and
  optional tests; ⚠ existing feature artifacts required synchronization
- Follow-up: existing automated test files are removed; future specs must describe manual checks
  instead of pytest tasks

Sync Impact Report (Amendment)
- Version change: 1.2.0 -> 1.3.0 (MINOR -- disposable-schema policy, module-size rule, and
  browser search replace the former social-network enrichment direction)
- Modified principles: I. Simplicity First -- a simple codebase still uses small, cohesive modules;
  III. Idempotent, Checkpointed Runs -- checkpointing applies within one schema version only;
  V. Mandatory Enrichment -- browser search is the M3 enrichment step; VI. One Retry Policy --
  applies to public web sources; VII. Disposable Schema, No Migrations -- added
- Modified sections: Technology Constraints and Development Workflow -- removed credentials and
  requirements tied to the former social-network candidate search; state persistence across a
  schema change is explicitly not required
- Templates checked: plan/spec/tasks templates remain compatible; no template edit required

Sync Impact Report (Amendment)
- Version change: 1.3.0 -> 1.3.1 (PATCH -- explicit browser-search exception for Playwright)
- Modified sections: Technology Constraints -- Playwright is permitted as the primary local browser
  driver for anonymous public search-result pages; for ordinary content pages it remains a fallback
  after plain HTTP
- Templates checked: plan/spec/tasks templates remain compatible; no template edit required
-->

# search-for-candidates Constitution

## Core Principles

### I. Simplicity First (NON-NEGOTIABLE)

Code MUST be a direct, readable implementation of the requirement in front of it. A Python
developer with ordinary university-level experience MUST be able to follow the main execution
path without first learning a framework or a catalogue of design patterns. Prefer plain functions,
small modules, explicit control flow, and ordinary data structures. A class is justified when it
owns real state or behavior; an interface, factory, generic repository, service layer, wrapper, or
other abstraction is justified only when it removes concrete duplication or complexity that exists
now. "Might need it later" is not a justification.

Simplicity does not mean putting an entire feature in one file. Each module MUST have one clear,
small responsibility, and a feature MUST be split when input/output, search, parsing, persistence,
or export logic would otherwise become mixed into one large file. Splitting code MUST clarify the
execution path, not create ceremonial layers or wrappers.

Feature code MUST NOT add routine logs, trace spans, metrics, telemetry, event buses, audit trails,
run-history models, or observability infrastructure. The CLI MAY print concise progress and failure
messages, and the program MUST still preserve useful errors, terminal statuses, retries, and
checkpoints required for correct operation. Additional diagnostics are allowed only for a concrete,
documented failure that cannot be diagnosed through those existing outputs.

Comments and docstrings MUST explain a non-obvious reason, external constraint, public contract, or
safety decision. They MUST NOT narrate obvious statements, repeat names, describe file layout, or
act as filler. Correctness, predictable failure handling and resumability MUST NOT be weakened in the
name of simplicity. The project MUST NOT create or maintain automated unit, integration, contract,
end-to-end, or other test suites. Each feature MUST be verified by manually running the relevant CLI
flow and reviewing its terminal result, SQLite state and exported XLSX. No frameworks or infrastructure beyond the concrete project
need (including Airflow, Kubernetes, microservices, or a second database) may be introduced. Any
remaining added complexity MUST be justified against a simpler alternative in the plan's Complexity
Tracking table.

Before writing custom code for a general problem, developers MUST evaluate the Python standard
library, an existing project dependency, and a mature focused dependency. If one meets the stated
requirements and constraints with fewer lines of project code, it MUST be used. A custom solution
is allowed only when the existing option fails a concrete requirement or adds more total complexity;
the rejected option and reason MUST be recorded in the plan's Complexity Tracking table.

### II. Batch CLI, Not a Service

This is a console command, not a web app or a service. There is no UI, no dashboard, and
no per-candidate click workflow — the operator runs one command and gets back
`output/candidates_*.xlsx`. Any feature that requires a server, a live UI, or an always-on
process is out of scope (`app-architecture.md`, §1, §10).

### III. Idempotent, Checkpointed Runs (NON-NEGOTIABLE)

Every pipeline step (layer1, VAK, match, layer2, browser search, export) MUST be resumable from
its last successfully completed checkpoint in `data/state.sqlite` while the schema has not changed.
A failure partway through a
multi-hour run (e.g., on university #47) MUST resume from that point on the next run, not
restart from zero — this is a hard requirement for a run that takes hours, not a
nice-to-have (`app-architecture.md`, §2).

### IV. Sites Are the Source of Truth, VAK Is Additive

`/sveden/employees` data is authoritative for who works where; VAK is a supplementary
signal, never a replacement. Matching MUST NOT collapse the two sources into one
undifferentiated record — a VAK-only record stays `vak_no_site` and a site-only record
stays `site_no_vak`, both first-class, not noise (`candidate-pipeline-architecture.md`, §1,
§4.2). Match-status naming MUST stay neutral — it describes which sources agree, not a
quality judgment — because "missing from one source" is the common case, not an error.

### V. Mandatory Enrichment, No Feedback Loop

Layer2 (contacts) and browser search run automatically on every **full (M3) run** for every eligible
candidate, once those steps are implemented — no per-record operator approval gate exists or
should be added for them. Earlier milestones (`app-architecture.md`, §8: M0–M2) MAY ship without
layer2/browser search altogether — a feature that delivers only layer1+VAK+matcher is a legitimate
incremental milestone, not a violation of this principle, as long as it does not add a per-record
approval gate for whichever steps it *does* implement, and fails fast rather than silently
no-op'ing if a caller tries to enable a step that isn't built yet. The xlsx is the only
deliverable: the program MUST NOT wait for, or persist, any reviewer decision back into
`state.sqlite`, and MUST NOT version a candidate's history across runs (`app-architecture.md`,
§10; `candidate-pipeline-architecture.md`, §7).

### VI. One Retry Policy, Not One Per Source

All outbound HTTP (VAK API, university sites, public search and result pages) MUST go through a single
retry/backoff-aware client wrapper rather than bespoke `try`/`except` logic duplicated in
each source module (`app-architecture.md`, §3.1).

### VII. Disposable Schema, No Migrations (NON-NEGOTIABLE)

The SQLite schema is changed directly when the requirement changes. The project MUST NOT add
migration scripts, schema-version tables, compatibility adapters, data-copy routines, or database
helper functions whose only purpose is preserving data between schema versions. State from an older
schema MAY be deleted and rebuilt by a new run; preserving it has no product value. Checkpoints and
backups exist only to recover an interrupted run using the same schema, not to provide historical
continuity.

## Technology Constraints

- **Stack**: Python 3.12+; `httpx` for ordinary HTTP. Playwright is permitted as the primary local
  browser driver for anonymous public search-result pages and as a fallback for ordinary pages that
  block plain HTTP; `lxml`/selectolax for `itemprop` parsing; SQLite (`data/state.sqlite`)
  for all pipeline state; `openpyxl` for the xlsx export; `concurrent.futures.ThreadPoolExecutor`
  (~10 workers) for per-domain parallelism in layer1/layer2. No Postgres, no message queue,
  no Docker/Kubernetes/Airflow for the MVP (`app-architecture.md`, §3).
- **Data scope**: only data that is either legally required to be public
  (`/sveden/employees` under Order No. 1493) or publicly visible on search-result and ordinary
  web pages — no private-data scraping, authentication bypass, or CAPTCHA bypass.
- **Secrets**: any future credentials live in `.env`, never committed; `university_registry` is
  maintained by hand and corrected via the `university_errors`
  xlsx sheet, not by an automated revalidation job (`candidate-pipeline-architecture.md`, §3).

## Development Workflow

- Build order follows the milestones in `app-architecture.md`, §8: **M0** registry +
  layer1 + export → **M1** + VAK + matcher → **M2** + layer2 contacts → **M3** + browser
  search. A full customer-facing run is M3; earlier milestones are valid, shippable
  checkpoints on their own, not "unfinished work."
- A schema change MAY discard `data/state.sqlite`; no migration or compatibility work is required.
  Backups are optional operational convenience, not a continuity guarantee.
- Every `/speckit-plan` for a feature in this repo MUST point back at the relevant
  section(s) of `research/pipeline/app-architecture.md` / `candidate-pipeline-architecture.md`
  rather than re-deriving architecture decisions already made there.
- The expanded Simplicity First rules in version 1.3.0 govern feature 002 and all later features.
  Existing code and Layer 2 work already started before this amendment do not require immediate
  rewriting. They adopt these rules when materially changed after merge or through a separate,
  explicitly approved simplification feature. A feature MUST NOT refactor unrelated legacy code
  merely to make it conform.

## Governance

This constitution supersedes ad-hoc technical decisions; where a plan or task conflicts
with it, the constitution wins unless amended first. Amendments are made by editing this
file directly, bumping the version per semantic versioning (MAJOR: a principle is removed
or redefined incompatibly; MINOR: a principle or section is added; PATCH: wording or
clarification only), and recording the change in a Sync Impact Report at the top of this
file. This project currently has one maintainer, so review is self-review — but a plan or
task that violates a principle here MUST NOT proceed without either documenting the
violation in the plan's Complexity Tracking table or amending this constitution first.

**Version**: 1.4.0 | **Ratified**: 2026-07-10 | **Last Amended**: 2026-07-15
