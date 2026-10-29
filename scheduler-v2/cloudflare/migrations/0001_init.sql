CREATE TABLE users (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);

CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER,
    revoked_at INTEGER
);

CREATE INDEX devices_by_user ON devices(user_id);

CREATE TABLE scheduled_jobs (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    session_scope_id TEXT NOT NULL,
    due_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'scheduled', 'waiting_for_device', 'delivering', 'delivered',
        'cancelled', 'expired', 'uncertain'
    )),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count BETWEEN 0 AND 5),
    next_attempt_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    CHECK (expires_at > due_at)
);

CREATE INDEX scheduled_jobs_due ON scheduled_jobs(next_attempt_at, id)
    WHERE state IN ('scheduled', 'waiting_for_device', 'delivering');
CREATE INDEX scheduled_jobs_per_session ON scheduled_jobs(device_id, session_scope_id)
    WHERE state IN ('scheduled', 'waiting_for_device', 'delivering');

CREATE TABLE delivery_attempts (
    job_id TEXT NOT NULL REFERENCES scheduled_jobs(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number BETWEEN 0 AND 5),
    attempted_at INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN (
        'sent', 'acknowledged', 'connection_unavailable', 'timeout', 'rejected', 'uncertain'
    )),
    PRIMARY KEY (job_id, attempt_number)
);
