// Package jobs defines the privacy boundary and state model for scheduled work.
package jobs

import (
	"errors"
	"fmt"
	"strings"
	"time"
)

const (
	MaxJobsPerSession  = 3
	MaxMessageBytes    = 20_000
	MaxDeliveryRetries = 5
)

type Provider string

const (
	ProviderCodex      Provider = "codex"
	ProviderClaudeCode Provider = "claude-code"
)

func (p Provider) Valid() bool {
	return p == ProviderCodex || p == ProviderClaudeCode
}

type State string

const (
	StatePendingRegistration State = "pending_registration"
	StateScheduled           State = "scheduled"
	StateWaitingForDevice    State = "waiting_for_device"
	StateDelivering          State = "delivering"
	StateDelivered           State = "delivered"
	StateCancelled           State = "cancelled"
	StateExpired             State = "expired"
	StateUncertain           State = "uncertain"
)

// LocalJob contains the message and host session mapping. It must never be
// serialized into requests sent to the scheduler service.
type LocalJob struct {
	ID             string    `json:"id"`
	SessionScopeID string    `json:"sessionScopeId"`
	Provider       Provider  `json:"provider"`
	SessionRef     string    `json:"sessionRef"`
	WorkDir        string    `json:"workDir"`
	Message        string    `json:"message"`
	DueAt          time.Time `json:"dueAt"`
	ExpiresAt      time.Time `json:"expiresAt"`
	State          State     `json:"state"`
	CreatedAt      time.Time `json:"createdAt"`
}

// ServerJob is the only job shape allowed across the device/server boundary.
// Keep it free of provider names, session references, and message content.
type ServerJob struct {
	ID             string    `json:"id"`
	SessionScopeID string    `json:"sessionScopeId"`
	DueAt          time.Time `json:"dueAt"`
	ExpiresAt      time.Time `json:"expiresAt"`
	State          State     `json:"state"`
	RetryCount     int       `json:"retryCount"`
	NextAttemptAt  time.Time `json:"nextAttemptAt,omitempty"`
}

// ServerMetadata intentionally projects only non-content fields from a local
// job. Use this function when creating or syncing remote records.
func (j LocalJob) ServerMetadata() ServerJob {
	return ServerJob{
		ID:             j.ID,
		SessionScopeID: j.SessionScopeID,
		DueAt:          j.DueAt,
		ExpiresAt:      j.ExpiresAt,
		State:          j.State,
	}
}

func (j LocalJob) Validate(now time.Time) error {
	if strings.TrimSpace(j.ID) == "" {
		return errors.New("job ID is required")
	}
	if strings.TrimSpace(j.SessionScopeID) == "" {
		return errors.New("session scope ID is required")
	}
	if !j.Provider.Valid() {
		return fmt.Errorf("unsupported provider %q", j.Provider)
	}
	if strings.TrimSpace(j.SessionRef) == "" {
		return errors.New("agent session reference is required")
	}
	if strings.TrimSpace(j.WorkDir) == "" {
		return errors.New("agent working directory is required")
	}
	if strings.TrimSpace(j.Message) == "" {
		return errors.New("message is required")
	}
	if len(j.Message) > MaxMessageBytes {
		return fmt.Errorf("message exceeds %d bytes", MaxMessageBytes)
	}
	if j.DueAt.IsZero() || !j.DueAt.After(now) {
		return errors.New("due time must be in the future")
	}
	if j.ExpiresAt.IsZero() || !j.ExpiresAt.After(j.DueAt) {
		return errors.New("expiry must be later than due time")
	}
	return nil
}

// RetryAt returns the time for retryNumber, where the first retry is one
// minute after due time and each subsequent delay grows by one minute.
// The resulting retry times are due+1m, due+3m, due+6m, due+10m, due+15m.
func RetryAt(dueAt time.Time, retryNumber int) (time.Time, error) {
	if retryNumber < 1 || retryNumber > MaxDeliveryRetries {
		return time.Time{}, fmt.Errorf("retry number must be between 1 and %d", MaxDeliveryRetries)
	}
	minutes := retryNumber * (retryNumber + 1) / 2
	return dueAt.Add(time.Duration(minutes) * time.Minute), nil
}

func (j ServerJob) Validate() error {
	if strings.TrimSpace(j.ID) == "" || strings.TrimSpace(j.SessionScopeID) == "" {
		return errors.New("job and session scope IDs are required")
	}
	if j.DueAt.IsZero() || j.ExpiresAt.IsZero() || !j.ExpiresAt.After(j.DueAt) {
		return errors.New("valid due and expiry times are required")
	}
	if j.RetryCount < 0 || j.RetryCount > MaxDeliveryRetries {
		return errors.New("retry count is out of range")
	}
	return nil
}
