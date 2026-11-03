package worker

import (
	"agent-scheduler/internal/jobs"
	"agent-scheduler/internal/localstore"
	"agent-scheduler/internal/usage"
	"context"
	"crypto/rand"
	"encoding/hex"
	"log"
	"time"
)

const continuationMessage = "Resume the task that was stopped when the usage limit was reached in this chat. Continue from where it left off, preserving the user's instructions and ordinary permissions. If no work remains, report that briefly."

// Only hook-created hints trigger history reads; idle chats are never scanned.
func (w Worker) processFailureEvents(ctx context.Context) {
	events, err := w.Store.DueFailureEvents(ctx, time.Now())
	if err != nil {
		log.Printf("read failure events: %v", err)
		return
	}
	for _, e := range events {
		retry, err := w.processFailureEvent(ctx, e)
		if err != nil {
			log.Printf("verify model failure: %v", err)
		}
		if retry && e.Attempts >= 5 {
			log.Printf("auto-continue verification exhausted; no continuation scheduled")
		}
		if err := w.Store.FinishFailureEvent(ctx, e, retry); err != nil {
			log.Printf("finish failure event: %v", err)
		}
	}
}
func (w Worker) processFailureEvent(ctx context.Context, e localstore.FailureEvent) (bool, error) {
	settings, err := w.Store.AutoContinueSettings(ctx)
	if err != nil {
		return true, err
	}
	var a localstore.AutoContinue
	for _, setting := range settings {
		if setting.SessionRef == e.SessionRef {
			a = setting
			break
		}
	}
	if !a.Enabled || a.Baseline == e.TurnID {
		return false, nil
	}
	turn, err := w.latestTurn(ctx, a.SessionRef)
	if err != nil {
		return true, err
	}
	// A hook runs before the terminal turn state is necessarily persisted.
	if turn.ID == "" || (turn.ID == e.TurnID && turn.Status == "inProgress") {
		return true, nil
	}
	if turn.ID != e.TurnID || !turn.UsageBlocked() {
		return false, nil
	}
	due, err := w.resetTime(ctx)
	if err != nil {
		return true, err
	}
	scope, err := w.Store.SessionScope(ctx, jobs.ProviderCodex, a.SessionRef)
	if err != nil {
		return true, err
	}
	var b [16]byte
	if _, err = rand.Read(b[:]); err != nil {
		return true, err
	}
	state := jobs.StateScheduled
	if w.Remote != nil {
		state = jobs.StatePendingRegistration
	}
	err = w.Store.CreateAutoContinue(ctx, jobs.LocalJob{ID: hex.EncodeToString(b[:]), SessionScopeID: scope, Provider: jobs.ProviderCodex, SessionRef: a.SessionRef, WorkDir: a.WorkDir, Message: continuationMessage, DueAt: due, ExpiresAt: due.Add(24 * time.Hour), State: state, CreatedAt: time.Now().UTC()}, turn.ID)
	return err != nil, err
}
func (w Worker) latestTurn(ctx context.Context, session string) (usage.Turn, error) {
	if w.readTurn != nil {
		return w.readTurn(ctx, session)
	}
	return usage.LatestTurn(ctx, "", session)
}
func (w Worker) resetTime(ctx context.Context) (time.Time, error) {
	if w.readReset != nil {
		return w.readReset(ctx)
	}
	return usage.CodexReset(ctx, "", time.Now())
}

// Validate again before dispatch: a later user turn supersedes an old failure.
func (w Worker) autoContinueCurrent(ctx context.Context, j jobs.LocalJob) (bool, error) {
	id, err := w.Store.AutoContinueTurn(ctx, j.ID)
	if err != nil || id == "" {
		return true, err
	}
	settings, err := w.Store.AutoContinueSettings(ctx)
	if err != nil {
		return false, err
	}
	enabled := false
	for _, a := range settings {
		if a.SessionRef == j.SessionRef {
			enabled = a.Enabled
		}
	}
	if !enabled {
		return false, nil
	}
	turn, err := w.latestTurn(ctx, j.SessionRef)
	if err != nil {
		return false, err
	}
	return turn.ID == id && turn.UsageBlocked(), nil
}
