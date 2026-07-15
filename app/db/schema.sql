-- Core pipeline MVP schema (see specs/001-core-pipeline-mvp/data-model.md)

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS universities (
    university_id INTEGER PRIMARY KEY AUTOINCREMENT,
    official_name TEXT NOT NULL,
    aliases TEXT,
    domain TEXT UNIQUE,
    region TEXT,
    accreditation_status TEXT,
    is_pilot INTEGER NOT NULL DEFAULT 0,
    layer1_status TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    is_full INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS employees_raw (
    employee_id INTEGER PRIMARY KEY AUTOINCREMENT,
    university_id INTEGER NOT NULL REFERENCES universities(university_id),
    fio TEXT NOT NULL,
    fio_normalized TEXT NOT NULL,
    post TEXT,
    degree TEXT,
    academic_title TEXT,
    department_raw TEXT,
    department_id TEXT,
    disciplines TEXT,
    gen_experience INTEGER,
    spec_experience INTEGER,
    teaching_level TEXT,
    employee_qualification TEXT,
    prof_development TEXT,
    teaching_op TEXT,
    identity_key TEXT NOT NULL,
    source_url TEXT NOT NULL,
    UNIQUE (university_id, identity_key)
);

CREATE TABLE IF NOT EXISTS vak_raw (
    vak_id TEXT PRIMARY KEY,
    old_id TEXT,
    fio TEXT NOT NULL,
    fio_normalized TEXT NOT NULL,
    dissertation_type TEXT NOT NULL,
    specialty TEXT,
    branch TEXT,
    topic TEXT,
    defend_org TEXT NOT NULL,
    council_cipher TEXT,
    org_address TEXT,
    org_phone TEXT,
    date_defend TEXT,
    is_pilot_branch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    identity_key TEXT,
    match_status TEXT NOT NULL CHECK (
        match_status IN (
            'site_and_vak',
            'site_and_vak_probable',
            'vak_no_site',
            'site_no_vak'
        )
    ),
    needs_review INTEGER NOT NULL DEFAULT 0,
    university_id INTEGER REFERENCES universities(university_id),
    department_id TEXT,
    post TEXT,
    degree TEXT,
    academic_title TEXT,
    disciplines TEXT,
    gen_experience INTEGER,
    spec_experience INTEGER,
    source_url TEXT,
    defenses TEXT,
    email TEXT,
    phone TEXT,
    contact_type TEXT,
    contact_source_url TEXT,
    candidate_content_hash TEXT NOT NULL,
    first_seen_run_id INTEGER NOT NULL REFERENCES runs(run_id),
    last_seen_run_id INTEGER NOT NULL REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS possible_namesakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    vak_candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    step TEXT NOT NULL CHECK (step IN ('layer1', 'vak', 'match', 'layer2', 'export')),
    university_id INTEGER REFERENCES universities(university_id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'error')),
    university_site_hash TEXT,
    checkpoint_cursor INTEGER,
    error_message TEXT,
    UNIQUE (run_id, step, university_id)
);

CREATE TABLE IF NOT EXISTS university_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    university_id INTEGER NOT NULL REFERENCES universities(university_id),
    error_type TEXT NOT NULL,
    message TEXT,
    last_attempt_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_employees_raw_university ON employees_raw(university_id);
CREATE INDEX IF NOT EXISTS idx_candidates_match_status ON candidates(match_status);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id, step);
CREATE INDEX IF NOT EXISTS idx_vak_raw_fio ON vak_raw(fio_normalized);
