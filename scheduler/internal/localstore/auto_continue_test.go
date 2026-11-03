package localstore

import (
	"agent-scheduler/internal/jobs"
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestPersistentContinuationLifecycle(t *testing.T) {
	ctx := context.Background()
	s, err := Open(filepath.Join(t.TempDir(), "jobs.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	a := AutoContinue{SessionRef: "chat", WorkDir: t.TempDir(), Enabled: true, Baseline: "old"}
	if err = s.SetAutoContinue(ctx, a); err != nil {
		t.Fatal(err)
	}
	scope, err := s.SessionScope(ctx, jobs.ProviderCodex, a.SessionRef)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	j := jobs.LocalJob{ID: "one", SessionScopeID: scope, Provider: jobs.ProviderCodex, SessionRef: a.SessionRef, WorkDir: a.WorkDir, Message: "continue", CreatedAt: now, DueAt: now.Add(time.Hour), ExpiresAt: now.Add(2 * time.Hour), State: jobs.StateScheduled}
	if err = s.CreateAutoContinue(ctx, j, "old"); err != nil {
		t.Fatal(err)
	}
	rows, _ := s.List(ctx, scope)
	if len(rows) != 0 {
		t.Fatal("baseline scheduled")
	}
	if err = s.CreateAutoContinue(ctx, j, "failure"); err != nil {
		t.Fatal(err)
	}
	j.ID = "two"
	if err = s.CreateAutoContinue(ctx, j, "failure"); err != nil {
		t.Fatal(err)
	}
	rows, _ = s.List(ctx, scope)
	if len(rows) != 1 {
		t.Fatalf("duplicate jobs: %d", len(rows))
	}
	a.Baseline = "new"
	if err = s.SetAutoContinue(ctx, a); err != nil {
		t.Fatal(err)
	}
	settings, _ := s.AutoContinueSettings(ctx)
	if settings[0].Baseline != "old" {
		t.Fatal("enable rewrote baseline")
	}
	a.Enabled = false
	if err = s.SetAutoContinue(ctx, a); err != nil {
		t.Fatal(err)
	}
	rows, _ = s.List(ctx, scope)
	if rows[0].State != jobs.StateCancelled {
		t.Fatal("disable left job active")
	}
	j.ID = "three"
	if err = s.CreateAutoContinue(ctx, j, "next"); err != nil {
		t.Fatal(err)
	}
	rows, _ = s.List(ctx, scope)
	if len(rows) != 1 {
		t.Fatal("disabled chat scheduled")
	}
}

func TestFailureEventRetryBudget(t *testing.T) {
	ctx := context.Background()
	s, err := Open(filepath.Join(t.TempDir(), "jobs.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err = s.SetAutoContinue(ctx, AutoContinue{SessionRef: "chat", WorkDir: t.TempDir(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	if err = s.RecordFailureEvent(ctx, "chat", "failure"); err != nil {
		t.Fatal(err)
	}
	for attempt := 0; attempt < 6; attempt++ {
		rows, err := s.DueFailureEvents(ctx, time.Now().Add(time.Hour))
		if err != nil {
			t.Fatal(err)
		}
		if len(rows) != 1 || rows[0].Attempts != attempt {
			t.Fatalf("attempt %d: %+v", attempt, rows)
		}
		if err = s.FinishFailureEvent(ctx, rows[0], true); err != nil {
			t.Fatal(err)
		}
	}
	rows, err := s.DueFailureEvents(ctx, time.Now().Add(time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 0 {
		t.Fatal("verification retries exceeded budget")
	}
}
