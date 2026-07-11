# search-for-candidates

Batch CLI pipeline: university Layer 1 employees, VAK defenses (list + detail), matcher, xlsx export.

**Architecture:** [research/pipeline/app-architecture.md](research/pipeline/app-architecture.md) (как устроено приложение), [research/pipeline/candidate-pipeline-architecture.md](research/pipeline/candidate-pipeline-architecture.md) (логика данных и матча).

## Pipeline (M1)

```text
registry CSV
    ├─ layer1 (/sveden/employees → program pages, parallel per university)
    └─ vak (list API → parallel detail fetch)     } ingest ∥ until both done
              ↓
           match (4 statuses + possible_namesakes)
              ↓
           export → site_employees + vak_candidates (+ errors, meta)
```

Layer2 contacts and VK are stubbed (`run.layer2` / `run.vk` must stay `false` in this build).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env
```

## Commands

```bash
python -m app run
python -m app run --full
python -m app step layer1
python -m app step vak
python -m app step match
python -m app export --out output/candidates.xlsx
python -m app status
python -m app reset
```

Configuration: `config.yaml`. Smoke: `config.smoke5.yaml` (5 universities, 15 VAK pages, ~4 min).

```bash
python -m app reset
python -m app --config config.smoke5.yaml run --out output/smoke5.xlsx --full
```

Pass config before the subcommand: `python -m app --config config.yaml run`.

Data: `data/university_registry.csv` (needs non-empty `domain` for layer1), state in `data/state.sqlite`, exports in `output/`.

See [specs/001-core-pipeline-mvp/quickstart.md](specs/001-core-pipeline-mvp/quickstart.md) for verification steps.
