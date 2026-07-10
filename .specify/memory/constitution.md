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
  clause: the "run automatically on every full run" language applies once layer2/VK are
  implemented and describes a full (M3) run; earlier milestones (M0–M2) shipping without
  layer2/VK are explicitly not a violation. Raised during `/speckit-analyze` on
  `specs/001-core-pipeline-mvp/` (finding C1): `plan.md`'s Constitution Check was reinterpreting
  this principle inline to justify feature 001 instead of the constitution stating the scope
  explicitly — this amendment removes the need for that inline reinterpretation.
- Added/removed sections: none
- Templates requiring updates: ✅ `specs/001-core-pipeline-mvp/plan.md` Constitution Check row
  for Principle V updated to cite the amended text directly (no other templates reference this
  principle by name)
-->

# search-for-candidates Constitution

## Core Principles

### I. Simplicity First (NON-NEGOTIABLE)

Code MUST be the shortest, most direct implementation that satisfies the concrete
requirement described in `research/pipeline/app-architecture.md` and
`research/pipeline/candidate-pipeline-architecture.md` — no speculative abstractions, no
frameworks or infrastructure beyond what those documents call for (no Airflow, Kubernetes,
microservices, or a second database for the MVP; see `app-architecture.md`, §3, §10). Any
added complexity — a new layer, a new abstraction, a new dependency — MUST be justified
against a simpler alternative in the plan's Complexity Tracking table before it is
accepted. "Might need it later" is not a justification; build for the task in front of you.

### II. Batch CLI, Not a Service

This is a console command, not a web app or a service. There is no UI, no dashboard, and
no "click to search VK" button — the operator runs one command and gets back
`output/candidates_*.xlsx`. Any feature that requires a server, a live UI, or an always-on
process is out of scope (`app-architecture.md`, §1, §10).

### III. Idempotent, Checkpointed Runs (NON-NEGOTIABLE)

Every pipeline step (layer1, VAK, match, layer2, VK, export) MUST be resumable from its
last successfully completed checkpoint in `data/state.sqlite`. A failure partway through a
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

Layer2 (contacts) and VK search run automatically on every **full (M3) run** for every eligible
candidate, once those steps are implemented — no per-record operator approval gate exists or
should be added for them. Earlier milestones (`app-architecture.md`, §8: M0–M2) MAY ship without
layer2/VK altogether — a feature that delivers only layer1+VAK+matcher is a legitimate
incremental milestone, not a violation of this principle, as long as it does not add a per-record
approval gate for whichever steps it *does* implement, and fails fast rather than silently
no-op'ing if a caller tries to enable a step that isn't built yet. The xlsx is the only
deliverable: the program MUST NOT wait for, or persist, any reviewer decision back into
`state.sqlite`, and MUST NOT version a candidate's history across runs (`app-architecture.md`,
§10; `candidate-pipeline-architecture.md`, §7).

### VI. One Retry Policy, Not One Per Source

All outbound HTTP (VAK API, university sites, VK API) MUST go through a single
retry/backoff-aware client wrapper rather than bespoke `try`/`except` logic duplicated in
each source module (`app-architecture.md`, §3.1).

## Technology Constraints

- **Stack**: Python 3.12+; `httpx` for HTTP (Playwright only as a fallback for sites that
  block plain HTTP); `lxml`/selectolax for `itemprop` parsing; SQLite (`data/state.sqlite`)
  for all pipeline state; `openpyxl` for the xlsx export; `concurrent.futures.ThreadPoolExecutor`
  (~10 workers) for per-domain parallelism in layer1/layer2. No Postgres, no message queue,
  no Docker/Kubernetes/Airflow for the MVP (`app-architecture.md`, §3).
- **Data scope**: only data that is either legally required to be public
  (`/sveden/employees` under Order No. 1493) or that the person made available themselves
  (VK profile search within a university's own community) — no private-data scraping.
- **Secrets**: `VK_TOKEN` and any other credentials live in `.env`, never committed;
  `university_registry` is maintained by hand and corrected via the `university_errors`
  xlsx sheet, not by an automated revalidation job (`candidate-pipeline-architecture.md`, §3).

## Development Workflow

- Build order follows the milestones in `app-architecture.md`, §8: **M0** registry +
  layer1 + export → **M1** + VAK + matcher → **M2** + layer2 contacts → **M3** + VK batch
  search. A full customer-facing run is M3; earlier milestones are valid, shippable
  checkpoints on their own, not "unfinished work."
- `data/state.sqlite` MUST be backed up automatically before `app reset` and after every
  successful `run` (`app-architecture.md`, §9).
- Every `/speckit-plan` for a feature in this repo MUST point back at the relevant
  section(s) of `research/pipeline/app-architecture.md` / `candidate-pipeline-architecture.md`
  rather than re-deriving architecture decisions already made there.

## Governance

This constitution supersedes ad-hoc technical decisions; where a plan or task conflicts
with it, the constitution wins unless amended first. Amendments are made by editing this
file directly, bumping the version per semantic versioning (MAJOR: a principle is removed
or redefined incompatibly; MINOR: a principle or section is added; PATCH: wording or
clarification only), and recording the change in a Sync Impact Report at the top of this
file. This project currently has one maintainer, so review is self-review — but a plan or
task that violates a principle here MUST NOT proceed without either documenting the
violation in the plan's Complexity Tracking table or amending this constitution first.

**Version**: 1.0.1 | **Ratified**: 2026-07-10 | **Last Amended**: 2026-07-10
