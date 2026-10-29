-- The server stores opaque scheduling metadata only. Do not add prompt text,
-- provider credentials, or real host session identifiers to these tables.
CREATE TABLE users (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE devices (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    public_key BYTEA NOT NULL,
    credential_hash BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX devices_by_user ON devices(user_id);

CREATE TABLE scheduled_jobs (
    id UUID PRIMARY KEY,
    device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    session_scope_id UUID NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'scheduled', 'waiting_for_device', 'delivering', 'delivered',
        'cancelled', 'expired', 'uncertain'
    )),
    retry_count SMALLINT NOT NULL DEFAULT 0 CHECK (retry_count BETWEEN 0 AND 5),
    next_attempt_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expires_at > due_at)
);

CREATE INDEX scheduled_jobs_due ON scheduled_jobs(next_attempt_at, id)
    WHERE state IN ('scheduled', 'waiting_for_device', 'delivering');
CREATE INDEX scheduled_jobs_per_session ON scheduled_jobs(device_id, session_scope_id)
    WHERE state IN ('scheduled', 'waiting_for_device', 'delivering');

CREATE TABLE delivery_attempts (
    job_id UUID NOT NULL REFERENCES scheduled_jobs(id) ON DELETE CASCADE,
    attempt_number SMALLINT NOT NULL CHECK (attempt_number BETWEEN 0 AND 5),
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    outcome TEXT NOT NULL CHECK (outcome IN (
        'sent', 'acknowledged', 'connection_unavailable', 'timeout', 'rejected', 'uncertain'
    )),
    PRIMARY KEY (job_id, attempt_number)
);
