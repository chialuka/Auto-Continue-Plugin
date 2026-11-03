package mcpserver

import (
	"agent-scheduler/internal/localstore"
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestOutcomeHook(t *testing.T) {
	ctx := context.Background()
	store, err := localstore.Open(filepath.Join(t.TempDir(), "jobs.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	s, _ := New(store, nil)
	if err = store.SetAutoContinue(ctx, localstore.AutoContinue{SessionRef: "chat", WorkDir: t.TempDir(), Enabled: true, Baseline: "baseline"}); err != nil {
		t.Fatal(err)
	}
	sub := "subagent"
	for _, in := range []ModelRequestOutcome{{SessionRef: "chat", TurnID: "ok", Status: "completed"}, {SessionRef: "disabled", TurnID: "failure", Status: "failed"}, {SessionRef: "chat", TurnID: "baseline", Status: "failed"}, {SessionRef: "chat", TurnID: "sub", Status: "failed", AgentID: &sub}} {
		if _, _, err = s.recordModelRequestOutcome(ctx, nil, in); err != nil {
			t.Fatal(err)
		}
	}
	rows, _ := store.DueFailureEvents(ctx, time.Now().Add(time.Minute))
	if len(rows) != 0 {
		t.Fatal("irrelevant outcome saved")
	}
	in := ModelRequestOutcome{SessionRef: "chat", TurnID: "failure", Status: "failed"}
	for i := 0; i < 2; i++ {
		if _, _, err = s.recordModelRequestOutcome(ctx, nil, in); err != nil {
			t.Fatal(err)
		}
	}
	rows, _ = store.DueFailureEvents(ctx, time.Now().Add(time.Minute))
	if len(rows) != 1 {
		t.Fatal("failure hints not deduplicated")
	}
}
