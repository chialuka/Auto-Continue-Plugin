// Package localstore persists the private part of scheduled jobs on the device.
package localstore

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite"

	"agent-scheduler/internal/jobs"
)

const activeStates = "('pending_registration','scheduled','waiting_for_device','delivering','uncertain')"

type Store struct {
	db *sql.DB
}

func Open(path string) (*Store, error) {
	if path == "" {
		return nil, errors.New("local database path is required")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, fmt.Errorf("create local data directory: %w", err)
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open local database: %w", err)
	}
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(`PRAGMA journal_mode = WAL;
		PRAGMA busy_timeout = 5000;
		PRAGMA foreign_keys = ON;
		CREATE TABLE IF NOT EXISTS local_jobs (
			id TEXT PRIMARY KEY,
			session_scope_id TEXT NOT NULL,
			provider TEXT NOT NULL CHECK(provider IN ('codex','claude-code')),
			session_ref TEXT NOT NULL,
			work_dir TEXT NOT NULL,
			message TEXT NOT NULL,
			due_at INTEGER NOT NULL,
			expires_at INTEGER NOT NULL,
			state TEXT NOT NULL,
			remote_registered INTEGER NOT NULL DEFAULT 0 CHECK(remote_registered IN (0,1)),
			remote_acked INTEGER NOT NULL DEFAULT 0 CHECK(remote_acked IN (0,1)),
			created_at INTEGER NOT NULL,
			CHECK(expires_at > due_at)
		);
		CREATE INDEX IF NOT EXISTS local_jobs_by_scope
			ON local_jobs(session_scope_id, created_at);
		CREATE INDEX IF NOT EXISTS local_jobs_due
			ON local_jobs(due_at, id) WHERE state IN ('pending_registration','scheduled','waiting_for_device');
		CREATE TABLE IF NOT EXISTS auto_continue_events (session_ref TEXT NOT NULL, turn_id TEXT NOT NULL, due_at INTEGER NOT NULL, attempts INTEGER NOT NULL, PRIMARY KEY(session_ref,turn_id));
		CREATE TABLE IF NOT EXISTS auto_continue (session_ref TEXT PRIMARY KEY, work_dir TEXT NOT NULL, enabled INTEGER NOT NULL, baseline TEXT NOT NULL);
		CREATE TABLE IF NOT EXISTS auto_continue_jobs (job_id TEXT PRIMARY KEY, session_ref TEXT NOT NULL, turn_id TEXT NOT NULL, UNIQUE(session_ref,turn_id));
		CREATE TABLE IF NOT EXISTS session_scopes (
			provider TEXT NOT NULL CHECK(provider IN ('codex','claude-code')),
			session_ref TEXT NOT NULL,
			scope_id TEXT NOT NULL UNIQUE,
			created_at INTEGER NOT NULL,
			PRIMARY KEY(provider, session_ref)
		);`); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("initialize local database: %w", err)
	}
	columns, err := db.Query(`PRAGMA table_info(local_jobs)`)
	if err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("inspect local database schema: %w", err)
	}
	hasRemoteAck := false
	for columns.Next() {
		var cid, notNull, primaryKey int
		var name, columnType string
		var defaultValue any
		if err := columns.Scan(&cid, &name, &columnType, &notNull, &defaultValue, &primaryKey); err != nil {
			_ = columns.Close()
			_ = db.Close()
			return nil, fmt.Errorf("read local database schema: %w", err)
		}
		if name == "remote_acked" {
			hasRemoteAck = true
		}
	}
	if err := columns.Close(); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("close local database schema query: %w", err)
	}
	if !hasRemoteAck {
		if _, err := db.Exec(`ALTER TABLE local_jobs ADD COLUMN remote_acked INTEGER NOT NULL DEFAULT 0 CHECK(remote_acked IN (0,1))`); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("upgrade local database schema: %w", err)
		}
	}
	if err := os.Chmod(path, 0o600); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("restrict local database permissions: %w", err)
	}
	return &Store{db: db}, nil
}

func (s *Store) Close() error { return s.db.Close() }

// SessionScope returns a stable pseudonymous scope for a provider session.
// The provider's real session reference stays in the local database.
func (s *Store) SessionScope(ctx context.Context, provider jobs.Provider, sessionRef string) (string, error) {
	if !provider.Valid() || sessionRef == "" {
		return "", fmt.Errorf("provider and session reference are required")
	}
	var scopeID string
	err := s.db.QueryRowContext(ctx, `SELECT scope_id FROM session_scopes WHERE provider = ? AND session_ref = ?`,
		provider, sessionRef).Scan(&scopeID)
	if err == nil {
		return scopeID, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return "", fmt.Errorf("load session scope: %w", err)
	}
	var random [16]byte
	if _, err := rand.Read(random[:]); err != nil {
		return "", fmt.Errorf("generate session scope ID: %w", err)
	}
	random[6] = (random[6] & 0x0f) | 0x40
	random[8] = (random[8] & 0x3f) | 0x80
	hexValue := hex.EncodeToString(random[:])
	scopeID = hexValue[:8] + "-" + hexValue[8:12] + "-" + hexValue[12:16] + "-" + hexValue[16:20] + "-" + hexValue[20:]
	if _, err := s.db.ExecContext(ctx, `INSERT INTO session_scopes(provider, session_ref, scope_id, created_at)
		VALUES (?, ?, ?, ?) ON CONFLICT(provider, session_ref) DO NOTHING`, provider, sessionRef, scopeID, time.Now().Unix()); err != nil {
		return "", fmt.Errorf("save session scope: %w", err)
	}
	if err := s.db.QueryRowContext(ctx, `SELECT scope_id FROM session_scopes WHERE provider = ? AND session_ref = ?`,
		provider, sessionRef).Scan(&scopeID); err != nil {
		return "", fmt.Errorf("reload session scope: %w", err)
	}
	return scopeID, nil
}

// Create stores the message locally and enforces the three-job session cap
// atomically. The caller should register the opaque ServerJob after this
// succeeds; on remote registration failure, it should leave the local job in
// a visible pending-sync state or roll it back explicitly.
func (s *Store) Create(ctx context.Context, job jobs.LocalJob) error {
	now := time.Now()
	if err := job.Validate(now); err != nil {
		return err
	}
	result, err := s.db.ExecContext(ctx, `
		INSERT INTO local_jobs
			(id, session_scope_id, provider, session_ref, work_dir, message, due_at, expires_at, state, created_at)
		SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
		WHERE (SELECT count(*) FROM local_jobs
			WHERE session_scope_id = ? AND state IN `+activeStates+`) < ?`,
		job.ID,
		job.SessionScopeID,
		job.Provider,
		job.SessionRef,
		job.WorkDir,
		job.Message,
		job.DueAt.Unix(),
		job.ExpiresAt.Unix(),
		job.State,
		now.Unix(),
		job.SessionScopeID,
		jobs.MaxJobsPerSession,
	)
	if err != nil {
		return fmt.Errorf("save local job: %w", err)
	}
	count, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("check local job insert: %w", err)
	}
	if count == 0 {
		return fmt.Errorf("session already has the maximum of %d active jobs", jobs.MaxJobsPerSession)
	}
	return nil
}

func (s *Store) Get(ctx context.Context, id string) (jobs.LocalJob, error) {
	var j jobs.LocalJob
	var due, expiry, created int64
	err := s.db.QueryRowContext(ctx, `
		SELECT id, session_scope_id, provider, session_ref, work_dir, message,
			due_at, expires_at, state, created_at
		FROM local_jobs WHERE id = ?`, id).Scan(
		&j.ID, &j.SessionScopeID, &j.Provider, &j.SessionRef, &j.WorkDir, &j.Message,
		&due, &expiry, &j.State, &created,
	)
	if err != nil {
		return jobs.LocalJob{}, err
	}
	j.DueAt = time.Unix(due, 0).UTC()
	j.ExpiresAt = time.Unix(expiry, 0).UTC()
	j.CreatedAt = time.Unix(created, 0).UTC()
	return j, nil
}

func (s *Store) List(ctx context.Context, scopeID string) ([]jobs.LocalJob, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, session_scope_id, provider, session_ref, work_dir, message,
			due_at, expires_at, state, created_at
		FROM local_jobs WHERE session_scope_id = ? ORDER BY created_at, id`, scopeID)
	if err != nil {
		return nil, fmt.Errorf("list local jobs: %w", err)
	}
	defer rows.Close()
	var result []jobs.LocalJob
	for rows.Next() {
		var j jobs.LocalJob
		var due, expiry, created int64
		if err := rows.Scan(
			&j.ID, &j.SessionScopeID, &j.Provider, &j.SessionRef, &j.WorkDir, &j.Message,
			&due, &expiry, &j.State, &created,
		); err != nil {
			return nil, fmt.Errorf("read local job: %w", err)
		}
		j.DueAt = time.Unix(due, 0).UTC()
		j.ExpiresAt = time.Unix(expiry, 0).UTC()
		j.CreatedAt = time.Unix(created, 0).UTC()
		result = append(result, j)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate local jobs: %w", err)
	}
	return result, nil
}

func (s *Store) Cancel(ctx context.Context, scopeID, id string) (bool, error) {
	result, err := s.db.ExecContext(ctx, `
		UPDATE local_jobs SET state = ?
		WHERE id = ? AND session_scope_id = ?
			AND state IN ('pending_registration','scheduled','waiting_for_device','uncertain')`,
		jobs.StateCancelled, id, scopeID,
	)
	if err != nil {
		return false, fmt.Errorf("cancel local job: %w", err)
	}
	count, err := result.RowsAffected()
	return count > 0, err
}

// ClaimDue changes one due local job to delivering. The state transition is
// conditional so reconnects or duplicate wake signals cannot claim it twice.
func (s *Store) ClaimDue(ctx context.Context, id string, now time.Time) (bool, error) {
	result, err := s.db.ExecContext(ctx, `
		UPDATE local_jobs SET state = ?
		WHERE id = ? AND state IN ('pending_registration','scheduled','waiting_for_device')
			AND due_at <= ? AND expires_at > ?`,
		jobs.StateDelivering, id, now.Unix(), now.Unix(),
	)
	if err != nil {
		return false, fmt.Errorf("claim due local job: %w", err)
	}
	count, err := result.RowsAffected()
	return count > 0, err
}

// DueJobs returns local jobs that are ready for the connector to claim.
func (s *Store) DueJobs(ctx context.Context, now time.Time) ([]jobs.LocalJob, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, session_scope_id, provider, session_ref, work_dir, message,
			due_at, expires_at, state, created_at
		FROM local_jobs WHERE state IN ('pending_registration','scheduled','waiting_for_device')
			AND due_at <= ? AND expires_at > ? ORDER BY due_at, id`, now.Unix(), now.Unix())
	if err != nil {
		return nil, fmt.Errorf("list due local jobs: %w", err)
	}
	defer rows.Close()
	var result []jobs.LocalJob
	for rows.Next() {
		var j jobs.LocalJob
		var due, expiry, created int64
		if err := rows.Scan(&j.ID, &j.SessionScopeID, &j.Provider, &j.SessionRef, &j.WorkDir, &j.Message,
			&due, &expiry, &j.State, &created); err != nil {
			return nil, fmt.Errorf("read due local job: %w", err)
		}
		j.DueAt, j.ExpiresAt, j.CreatedAt = time.Unix(due, 0).UTC(), time.Unix(expiry, 0).UTC(), time.Unix(created, 0).UTC()
		result = append(result, j)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate due local jobs: %w", err)
	}
	return result, nil
}

func (s *Store) ExpireDue(ctx context.Context, now time.Time) error {
	_, err := s.db.ExecContext(ctx, `UPDATE local_jobs SET state = ?
		WHERE state IN ('pending_registration','scheduled','waiting_for_device') AND expires_at <= ?`,
		jobs.StateExpired, now.Unix())
	if err != nil {
		return fmt.Errorf("expire local jobs: %w", err)
	}
	return nil
}

func (s *Store) SetState(ctx context.Context, id string, state jobs.State) error {
	result, err := s.db.ExecContext(ctx, `UPDATE local_jobs SET state = ? WHERE id = ?`, state, id)
	if err != nil {
		return fmt.Errorf("update local job state: %w", err)
	}
	count, err := result.RowsAffected()
	if err == nil && count == 0 {
		return sql.ErrNoRows
	}
	return err
}

func (s *Store) MarkRemoteRegistered(ctx context.Context, id string) error {
	result, err := s.db.ExecContext(ctx, `
		UPDATE local_jobs SET
			state = CASE WHEN state = ? THEN ? ELSE state END,
			remote_registered = 1
		WHERE id = ? AND remote_registered = 0`, jobs.StatePendingRegistration, jobs.StateScheduled, id)
	if err != nil {
		return fmt.Errorf("mark remote job registered: %w", err)
	}
	count, err := result.RowsAffected()
	if err == nil && count == 0 {
		return sql.ErrNoRows
	}
	return err
}

func (s *Store) PendingRegistrations(ctx context.Context) ([]jobs.LocalJob, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, session_scope_id, provider, session_ref, work_dir, message,
			due_at, expires_at, state, created_at
		FROM local_jobs WHERE remote_registered = 0
			AND state IN ('pending_registration','scheduled') ORDER BY created_at, id`)
	if err != nil {
		return nil, fmt.Errorf("list pending registrations: %w", err)
	}
	defer rows.Close()
	var result []jobs.LocalJob
	for rows.Next() {
		var j jobs.LocalJob
		var due, expiry, created int64
		if err := rows.Scan(
			&j.ID, &j.SessionScopeID, &j.Provider, &j.SessionRef, &j.WorkDir, &j.Message,
			&due, &expiry, &j.State, &created,
		); err != nil {
			return nil, fmt.Errorf("read pending registration: %w", err)
		}
		j.DueAt = time.Unix(due, 0).UTC()
		j.ExpiresAt = time.Unix(expiry, 0).UTC()
		j.CreatedAt = time.Unix(created, 0).UTC()
		result = append(result, j)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate pending registrations: %w", err)
	}
	return result, nil
}

func (s *Store) PendingRemoteAcknowledgements(ctx context.Context) ([]jobs.LocalJob, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, session_scope_id, provider, session_ref, work_dir, message,
			due_at, expires_at, state, created_at
		FROM local_jobs WHERE remote_registered = 1 AND remote_acked = 0
			AND state IN ('delivered','cancelled') ORDER BY created_at, id`)
	if err != nil {
		return nil, fmt.Errorf("list pending server acknowledgements: %w", err)
	}
	defer rows.Close()
	var result []jobs.LocalJob
	for rows.Next() {
		var j jobs.LocalJob
		var due, expiry, created int64
		if err := rows.Scan(&j.ID, &j.SessionScopeID, &j.Provider, &j.SessionRef, &j.WorkDir, &j.Message,
			&due, &expiry, &j.State, &created); err != nil {
			return nil, fmt.Errorf("read pending server acknowledgement: %w", err)
		}
		j.DueAt, j.ExpiresAt, j.CreatedAt = time.Unix(due, 0).UTC(), time.Unix(expiry, 0).UTC(), time.Unix(created, 0).UTC()
		result = append(result, j)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate pending server acknowledgements: %w", err)
	}
	return result, nil
}

func (s *Store) MarkRemoteAcknowledged(ctx context.Context, id string) error {
	result, err := s.db.ExecContext(ctx, `UPDATE local_jobs SET remote_acked = 1 WHERE id = ? AND remote_acked = 0`, id)
	if err != nil {
		return fmt.Errorf("mark remote acknowledgement: %w", err)
	}
	count, err := result.RowsAffected()
	if err == nil && count == 0 {
		return sql.ErrNoRows
	}
	return err
}
