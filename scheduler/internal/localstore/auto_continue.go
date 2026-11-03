package localstore

import (
	"agent-scheduler/internal/jobs"
	"context"
	"database/sql"
	"errors"
)

type AutoContinue struct {
	SessionRef string `json:"sessionRef"`
	WorkDir    string `json:"workDir"`
	Enabled    bool   `json:"enabled"`
	Baseline   string `json:"baselineTurnId"`
}

func (s *Store) SetAutoContinue(ctx context.Context, a AutoContinue) error {
	if a.SessionRef == "" {
		return errors.New("session reference is required")
	}
	// Repeated enable calls preserve the baseline; disable cancels only watcher jobs.
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, err = tx.ExecContext(ctx, `INSERT INTO auto_continue VALUES (?,?,?,?) ON CONFLICT(session_ref) DO UPDATE SET work_dir=excluded.work_dir, enabled=excluded.enabled, baseline=CASE WHEN auto_continue.enabled=1 AND excluded.enabled=1 THEN auto_continue.baseline ELSE excluded.baseline END`, a.SessionRef, a.WorkDir, a.Enabled, a.Baseline)
	if err != nil {
		return err
	}
	if !a.Enabled {
		_, err = tx.ExecContext(ctx, `UPDATE local_jobs SET state='cancelled' WHERE id IN (SELECT job_id FROM auto_continue_jobs WHERE session_ref=?) AND state IN ('pending_registration','scheduled','waiting_for_device')`, a.SessionRef)
		if err != nil {
			return err
		}
	}
	return tx.Commit()
}
func (s *Store) AutoContinueSettings(ctx context.Context) ([]AutoContinue, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT session_ref,work_dir,enabled,baseline FROM auto_continue`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []AutoContinue
	for rows.Next() {
		var a AutoContinue
		if err := rows.Scan(&a.SessionRef, &a.WorkDir, &a.Enabled, &a.Baseline); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}
func (s *Store) AutoContinueTurn(ctx context.Context, id string) (string, error) {
	var turn string
	err := s.db.QueryRowContext(ctx, `SELECT turn_id FROM auto_continue_jobs WHERE job_id=?`, id).Scan(&turn)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return turn, err
}

// CreateAutoContinue atomically deduplicates failures across resident and MCP workers.
func (s *Store) CreateAutoContinue(ctx context.Context, j jobs.LocalJob, turn string) error {
	if err := j.Validate(j.CreatedAt); err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `INSERT INTO auto_continue_jobs SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM auto_continue WHERE session_ref=? AND enabled=1 AND baseline<>?) AND NOT EXISTS (SELECT 1 FROM auto_continue_jobs WHERE session_ref=? AND turn_id=?) AND (SELECT COUNT(*) FROM local_jobs WHERE session_scope_id=? AND state IN `+activeStates+`) < ?`, j.ID, j.SessionRef, turn, j.SessionRef, turn, j.SessionRef, turn, j.SessionScopeID, jobs.MaxJobsPerSession)
	if err != nil {
		return err
	}
	n, _ := result.RowsAffected()
	if n == 0 {
		return nil
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO local_jobs (id,session_scope_id,provider,session_ref,work_dir,message,due_at,expires_at,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)`, j.ID, j.SessionScopeID, j.Provider, j.SessionRef, j.WorkDir, j.Message, j.DueAt.Unix(), j.ExpiresAt.Unix(), j.State, j.CreatedAt.Unix())
	if err != nil {
		return err
	}
	return tx.Commit()
}
