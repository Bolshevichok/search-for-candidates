# search-for-candidates

Batch CLI pipeline: university Layer 1 employees, VAK defenses, matcher, xlsx export.

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

Configuration: `config.yaml` (`run.layer2` and `run.vk` must stay `false` in this build). Pass config before the subcommand, e.g. `python -m app --config config.yaml run`.

Data: `data/university_registry.csv`, state in `data/state.sqlite`, exports in `output/`.
