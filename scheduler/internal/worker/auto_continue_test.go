package worker

import (
	"agent-scheduler/internal/jobs"
	"agent-scheduler/internal/localstore"
	"agent-scheduler/internal/usage"
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"testing"
	"time"
)

func TestFailureEventVerification(t *testing.T) {
	for _, status := range []string{"inProgress", "completed", "failed", "superseded"} {
		t.Run(status, func(t *testing.T) {
			ctx := context.Background()
			store, err := localstore.Open(filepath.Join(t.TempDir(), "jobs.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			if err = store.SetAutoContinue(ctx, localstore.AutoContinue{SessionRef: "chat", WorkDir: t.TempDir(), Enabled: true, Baseline: "baseline"}); err != nil {
				t.Fatal(err)
			}
			var turn usage.Turn
			raw := `{"id":"failure","status":"` + status + `","error":{"codexErrorInfo":"usageLimitExceeded"}}`
			if err = json.Unmarshal([]byte(raw), &turn); err != nil {
				t.Fatal(err)
			}
			if status == "superseded" {
				turn.ID = "later"
				turn.Status = "inProgress"
			}
			resets := 0
			w := Worker{Store: store, readTurn: func(context.Context, string) (usage.Turn, error) { return turn, nil }, readReset: func(context.Context) (time.Time, error) { resets++; return time.Now().Add(time.Hour), nil }}
			event := localstore.FailureEvent{SessionRef: "chat", TurnID: "failure"}
			retry, err := w.processFailureEvent(ctx, event)
			if err != nil {
				t.Fatal(err)
			}
			if retry != (status == "inProgress") {
				t.Fatalf("retry=%v", retry)
			}
			scope, _ := store.SessionScope(ctx, "codex", "chat")
			rows, _ := store.List(ctx, scope)
			want := 0
			if status == "failed" {
				want = 1
			}
			if len(rows) != want || resets != want {
				t.Fatalf("jobs=%d resets=%d", len(rows), resets)
			}
			if status == "failed" {
				if _, err = w.processFailureEvent(ctx, event); err != nil {
					t.Fatal(err)
				}
				rows, _ = store.List(ctx, scope)
				if len(rows) != 1 {
					t.Fatal("duplicate scheduled")
				}
			}
		})
	}
}

type countingDispatcher struct{ calls int }

func (d *countingDispatcher) Dispatch(_ context.Context, _ jobs.LocalJob) error {
	d.calls++
	return nil
}
func TestHookHintToDelivery(t *testing.T) {
	for _, superseded := range []bool{false, true} {
		t.Run(fmt.Sprint(superseded), func(t *testing.T) {
			ctx := context.Background()
			store, err := localstore.Open(filepath.Join(t.TempDir(), "jobs.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			if err = store.SetAutoContinue(ctx, localstore.AutoContinue{SessionRef: "chat", WorkDir: t.TempDir(), Enabled: true}); err != nil {
				t.Fatal(err)
			}
			if err = store.RecordFailureEvent(ctx, "chat", "failure"); err != nil {
				t.Fatal(err)
			}
			events, err := store.DueFailureEvents(ctx, time.Now().Add(time.Minute))
			if err != nil || len(events) != 1 {
				t.Fatalf("hint missing: %v", err)
			}
			var turn usage.Turn
			if err = json.Unmarshal([]byte(`{"id":"failure","status":"failed","error":{"codexErrorInfo":"usageLimitExceeded"}}`), &turn); err != nil {
				t.Fatal(err)
			}
			dispatcher := &countingDispatcher{}
			w := Worker{Store: store, Dispatcher: dispatcher, readTurn: func(context.Context, string) (usage.Turn, error) { return turn, nil }, readReset: func(context.Context) (time.Time, error) { return time.Now().Add(time.Hour), nil }}
			retry, err := w.processFailureEvent(ctx, events[0])
			if err != nil || retry {
				t.Fatalf("schedule: retry=%v error=%v", retry, err)
			}
			scope, _ := store.SessionScope(ctx, "codex", "chat")
			scheduled, _ := store.List(ctx, scope)
			if len(scheduled) != 1 {
				t.Fatal("job missing")
			}
			if superseded {
				turn.ID = "later"
				turn.Status = "completed"
			}
			w.deliver(ctx, scheduled[0], 0, scheduled[0].DueAt.Add(time.Second))
			w.deliver(ctx, scheduled[0], 0, scheduled[0].DueAt.Add(time.Second))
			saved, err := store.Get(ctx, scheduled[0].ID)
			if err != nil {
				t.Fatal(err)
			}
			expected := jobs.StateDelivered
			calls := 1
			if superseded {
				expected = jobs.StateCancelled
				calls = 0
			}
			if saved.State != expected || dispatcher.calls != calls {
				t.Fatalf("state=%s calls=%d", saved.State, dispatcher.calls)
			}
		})
	}
}
