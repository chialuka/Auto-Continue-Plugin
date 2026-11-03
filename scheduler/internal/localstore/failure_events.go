package localstore

import (
	"context"
	"time"
)

// FailureEvent is a durable hint from a model-request hook, not proof of a failed turn.
type FailureEvent struct {
	SessionRef, TurnID string
	Attempts           int
}

func (s *Store) RecordFailureEvent(ctx context.Context, session, turn string) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO auto_continue_events(session_ref,turn_id,due_at,attempts) SELECT ?,?,?,0 WHERE EXISTS (SELECT 1 FROM auto_continue WHERE session_ref=? AND enabled=1 AND baseline<>?) ON CONFLICT(session_ref,turn_id) DO NOTHING`, session, turn, time.Now().Add(2*time.Second).Unix(), session, turn)
	return err
}
func (s *Store) DueFailureEvents(ctx context.Context, now time.Time) ([]FailureEvent, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT session_ref,turn_id,attempts FROM auto_continue_events WHERE due_at<=?`, now.Unix())
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []FailureEvent
	for rows.Next() {
		var e FailureEvent
		if err := rows.Scan(&e.SessionRef, &e.TurnID, &e.Attempts); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}
func (s *Store) FinishFailureEvent(ctx context.Context, e FailureEvent, retry bool) error {
	if retry && e.Attempts < 5 {
		_, err := s.db.ExecContext(ctx, `UPDATE auto_continue_events SET attempts=attempts+1,due_at=? WHERE session_ref=? AND turn_id=? AND attempts=?`, time.Now().Add(10*time.Second).Unix(), e.SessionRef, e.TurnID, e.Attempts)
		return err
	}
	_, err := s.db.ExecContext(ctx, `DELETE FROM auto_continue_events WHERE session_ref=? AND turn_id=?`, e.SessionRef, e.TurnID)
	return err
}
