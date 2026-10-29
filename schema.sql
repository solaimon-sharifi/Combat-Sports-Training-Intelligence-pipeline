-- Combat Sports Training Intelligence — normalized schema
-- SQLite dialect. Run via scripts/init_db.py.

PRAGMA foreign_keys = ON;

-- ============================================================
-- Lookup tables (controlled vocabulary)
-- ============================================================
-- Free text on `position` and `technique` breaks aggregation queries
-- ("half guard" vs "Half Guard" vs "halfguard" become three different
-- groups). Lookup tables force every exchange to reference one of a
-- fixed set of values, so GROUP BY actually groups.

CREATE TABLE IF NOT EXISTS disciplines (
    discipline_id   INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE          -- BJJ, MMA, Wrestling, Judo, No-Gi
);

CREATE TABLE IF NOT EXISTS exchange_types (
    exchange_type_id INTEGER PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE         -- submission, takedown, sweep, escape, pass, reversal
);

CREATE TABLE IF NOT EXISTS positions (
    position_id     INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE          -- closed_guard, half_guard, mount, back, side_control, standing, turtle, open_guard
);

CREATE TABLE IF NOT EXISTS techniques (
    technique_id    INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,         -- armbar, triangle, double_leg, kimura, rear_naked_choke, ...
    exchange_type_id INTEGER NOT NULL REFERENCES exchange_types(exchange_type_id)
);

-- ============================================================
-- Core entities
-- ============================================================

CREATE TABLE IF NOT EXISTS opponents (
    opponent_id     INTEGER PRIMARY KEY,
    label           TEXT,                          -- optional display name/handle, not required
    belt_rank       REAL NOT NULL CHECK (belt_rank >= 0 AND belt_rank <= 4.9),
                                                     -- white=0, blue=1, purple=2, brown=3, black=4
                                                     -- stripes as decimals, e.g. blue+2 stripes = 1.2
    weight_class    TEXT                            -- optional, free text acceptable (not analyzed positionally)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id      INTEGER PRIMARY KEY,
    date            TEXT NOT NULL,                  -- ISO 8601 'YYYY-MM-DD'
    discipline_id   INTEGER NOT NULL REFERENCES disciplines(discipline_id),
    duration_min    INTEGER NOT NULL CHECK (duration_min > 0),
    rounds          INTEGER NOT NULL CHECK (rounds >= 0),
    notes           TEXT,
    is_synthetic    INTEGER NOT NULL DEFAULT 0      -- 1 = generated test data, 0 = real logged session
);

CREATE TABLE IF NOT EXISTS exchanges (
    exchange_id     INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    opponent_id     INTEGER REFERENCES opponents(opponent_id),
    exchange_type_id INTEGER NOT NULL REFERENCES exchange_types(exchange_type_id),
    direction       TEXT NOT NULL CHECK (direction IN ('for', 'against')),
    technique_id    INTEGER REFERENCES techniques(technique_id),
    position_id     INTEGER NOT NULL REFERENCES positions(position_id),
    outcome         TEXT NOT NULL CHECK (outcome IN ('successful', 'failed', 'defended'))
);

CREATE INDEX IF NOT EXISTS idx_exchanges_session ON exchanges(session_id);
CREATE INDEX IF NOT EXISTS idx_exchanges_opponent ON exchanges(opponent_id);
CREATE INDEX IF NOT EXISTS idx_exchanges_type ON exchanges(exchange_type_id);
CREATE INDEX IF NOT EXISTS idx_exchanges_position ON exchanges(position_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);

-- AI film reviews produced by app/server.py. Not tied to a session on purpose:
-- a clip can be reviewed before it is logged, or without being logged at all.
CREATE TABLE IF NOT EXISTS film_reviews (
    review_id    INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    mode         TEXT NOT NULL CHECK (mode IN ('upload', 'live')),
    context      TEXT,
    frame_count  INTEGER NOT NULL CHECK (frame_count > 0),
    feedback     TEXT NOT NULL
);

-- ============================================================
-- Seed lookup values
-- ============================================================

INSERT OR IGNORE INTO disciplines (name) VALUES
    ('BJJ'), ('No-Gi'), ('MMA'), ('Wrestling'), ('Judo');

INSERT OR IGNORE INTO exchange_types (name) VALUES
    ('submission'), ('takedown'), ('sweep'), ('escape'), ('pass'), ('reversal');

INSERT OR IGNORE INTO positions (name) VALUES
    ('standing'), ('closed_guard'), ('open_guard'), ('half_guard'),
    ('side_control'), ('mount'), ('back'), ('turtle'), ('north_south');

INSERT OR IGNORE INTO techniques (name, exchange_type_id) VALUES
    ('armbar',            (SELECT exchange_type_id FROM exchange_types WHERE name='submission')),
    ('triangle',          (SELECT exchange_type_id FROM exchange_types WHERE name='submission')),
    ('rear_naked_choke',  (SELECT exchange_type_id FROM exchange_types WHERE name='submission')),
    ('kimura',            (SELECT exchange_type_id FROM exchange_types WHERE name='submission')),
    ('guillotine',        (SELECT exchange_type_id FROM exchange_types WHERE name='submission')),
    ('darce',             (SELECT exchange_type_id FROM exchange_types WHERE name='submission')),
    ('ezekiel',           (SELECT exchange_type_id FROM exchange_types WHERE name='submission')),
    ('americana',         (SELECT exchange_type_id FROM exchange_types WHERE name='submission')),
    ('double_leg',        (SELECT exchange_type_id FROM exchange_types WHERE name='takedown')),
    ('single_leg',        (SELECT exchange_type_id FROM exchange_types WHERE name='takedown')),
    ('ankle_pick',        (SELECT exchange_type_id FROM exchange_types WHERE name='takedown')),
    ('body_lock',         (SELECT exchange_type_id FROM exchange_types WHERE name='takedown')),
    ('foot_sweep',        (SELECT exchange_type_id FROM exchange_types WHERE name='sweep')),
    ('scissor_sweep',     (SELECT exchange_type_id FROM exchange_types WHERE name='sweep')),
    ('hip_bump_sweep',    (SELECT exchange_type_id FROM exchange_types WHERE name='sweep')),
    ('elbow_escape',      (SELECT exchange_type_id FROM exchange_types WHERE name='escape')),
    ('bridge_and_roll',   (SELECT exchange_type_id FROM exchange_types WHERE name='escape')),
    ('knee_shield',       (SELECT exchange_type_id FROM exchange_types WHERE name='escape')),
    ('guard_pass_generic',(SELECT exchange_type_id FROM exchange_types WHERE name='pass')),
    ('back_take',         (SELECT exchange_type_id FROM exchange_types WHERE name='reversal'));
