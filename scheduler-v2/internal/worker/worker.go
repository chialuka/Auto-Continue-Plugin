// Package worker runs local due jobs while the connector process is alive.
package worker

import (
	"context"
	"log"
	"time"

	"agent-scheduler/internal/controlplane"
	"agent-scheduler/internal/host"
	"agent-scheduler/internal/jobs"
	"agent-scheduler/internal/localstore"
)

type Worker struct {
	Store      *localstore.Store
	Dispatcher host.Dispatcher
	Remote     *controlplane.Client
	Interval   time.Duration
}

func (w Worker) Run(ctx context.Context) {
	interval := w.Interval
	if interval <= 0 {
		interval = time.Second
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	lastRemoteSync := time.Time{}
	for {
		if w.Remote != nil && (lastRemoteSync.IsZero() || time.Since(lastRemoteSync) >= 30*time.Second) {
			w.syncRemote(ctx)
			lastRemoteSync = time.Now()
		}
		w.runOnce(ctx)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (w Worker) runOnce(ctx context.Context) {
	now := time.Now().UTC()
	if err := w.Store.ExpireDue(ctx, now); err != nil {
		log.Printf("expire scheduled messages: %v", err)
	}
	items, err := w.Store.DueJobs(ctx, now)
	if err != nil {
		log.Printf("load due scheduled messages: %v", err)
		return
	}
	for _, job := range items {
		w.deliver(ctx, job, 0, now)
	}
}

func (w Worker) HandleSignal(ctx context.Context, signal controlplane.DeviceSignal) {
	job, err := w.Store.Get(ctx, signal.JobID)
	if err != nil {
		log.Printf("load local scheduled message: %v", err)
		w.ack(ctx, signal.JobID, signal.Attempt, "connection_unavailable")
		return
	}
	if job.State == jobs.StateDelivered {
		w.ack(ctx, signal.JobID, signal.Attempt, "acknowledged")
		return
	}
	if job.State == jobs.StateUncertain {
		w.ack(ctx, signal.JobID, signal.Attempt, "uncertain")
		return
	}
	if job.State == jobs.StateCancelled || job.State == jobs.StateExpired {
		w.ack(ctx, signal.JobID, signal.Attempt, "rejected")
		return
	}
	if !job.DueAt.After(time.Now()) && job.ExpiresAt.After(time.Now()) {
		w.deliver(ctx, job, signal.Attempt, time.Now().UTC())
		return
	}
	w.ack(ctx, signal.JobID, signal.Attempt, "rejected")
}

func (w Worker) deliver(ctx context.Context, job jobs.LocalJob, attempt int, now time.Time) {
	claimed, err := w.Store.ClaimDue(ctx, job.ID, now)
	if err != nil {
		log.Printf("claim scheduled message %s: %v", job.ID, err)
		w.ack(ctx, job.ID, attempt, "timeout")
		return
	}
	if !claimed {
		current, getErr := w.Store.Get(ctx, job.ID)
		if getErr == nil && current.State == jobs.StateDelivered {
			w.ack(ctx, job.ID, attempt, "acknowledged")
		} else if getErr == nil && current.State == jobs.StateDelivering {
			return
		} else {
			w.ack(ctx, job.ID, attempt, "rejected")
		}
		return
	}
	if err := w.Dispatcher.Dispatch(ctx, job); err != nil {
		log.Printf("deliver scheduled message %s: %v", job.ID, err)
		if stateErr := w.Store.SetState(ctx, job.ID, jobs.StateUncertain); stateErr != nil {
			log.Printf("mark scheduled message %s uncertain: %v", job.ID, stateErr)
		}
		w.ack(ctx, job.ID, attempt, "uncertain")
		return
	}
	if err := w.Store.SetState(ctx, job.ID, jobs.StateDelivered); err != nil {
		log.Printf("mark scheduled message %s delivered: %v", job.ID, err)
		w.ack(ctx, job.ID, attempt, "uncertain")
		return
	}
	w.ack(ctx, job.ID, attempt, "acknowledged")
}

func (w Worker) ack(ctx context.Context, id string, attempt int, outcome string) {
	if w.Remote == nil {
		return
	}
	if err := w.Remote.AckJob(ctx, id, attempt, outcome); err != nil {
		log.Printf("acknowledge scheduled message %s: %v", id, err)
		return
	}
	if outcome == "acknowledged" {
		if err := w.Store.MarkRemoteAcknowledged(ctx, id); err != nil {
			log.Printf("mark remote acknowledgement for %s: %v", id, err)
		}
	}
}

func (w Worker) syncRemote(ctx context.Context) {
	registrations, err := w.Store.PendingRegistrations(ctx)
	if err != nil {
		log.Printf("load pending scheduler registrations: %v", err)
		return
	}
	for _, job := range registrations {
		metadata := job.ServerMetadata()
		metadata.State = jobs.StateScheduled
		if _, err := w.Remote.CreateJob(ctx, metadata); err != nil {
			log.Printf("register scheduled message %s: %v", job.ID, err)
			continue
		}
		if err := w.Store.MarkRemoteRegistered(ctx, job.ID); err != nil {
			log.Printf("mark scheduled message %s registered: %v", job.ID, err)
		}
	}
	acknowledgements, err := w.Store.PendingRemoteAcknowledgements(ctx)
	if err != nil {
		log.Printf("load pending server acknowledgements: %v", err)
		return
	}
	for _, job := range acknowledgements {
		if job.State == jobs.StateDelivered {
			if err := w.Remote.AckJob(ctx, job.ID, 0, "acknowledged"); err != nil {
				log.Printf("sync completed scheduled message %s: %v", job.ID, err)
				continue
			}
		} else if err := w.Remote.CancelJob(ctx, job.ID); err != nil {
			log.Printf("sync cancelled scheduled message %s: %v", job.ID, err)
			continue
		}
		if err := w.Store.MarkRemoteAcknowledged(ctx, job.ID); err != nil {
			log.Printf("mark synced scheduled message %s: %v", job.ID, err)
		}
	}
}

func (w Worker) RunStream(ctx context.Context) {
	if w.Remote == nil {
		return
	}
	delay := time.Second
	for ctx.Err() == nil {
		err := w.Remote.Stream(ctx, w.HandleSignal)
		if ctx.Err() != nil {
			return
		}
		if err != nil {
			log.Printf("scheduler stream disconnected: %v", err)
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
		if delay < time.Minute {
			delay *= 2
			if delay > time.Minute {
				delay = time.Minute
			}
		}
	}
}
