// Package mcpserver exposes agent-neutral scheduling tools over stdio MCP.
package mcpserver

import (
	"context"
	"crypto/rand"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"agent-scheduler/internal/controlplane"
	"agent-scheduler/internal/jobs"
	"agent-scheduler/internal/localstore"
	"agent-scheduler/internal/usage"
)

// Resolve context in the calling agent, not the possibly shared MCP process.
const contextInstructions = " For the current Codex chat, obtain sessionRef by reading CODEX_THREAD_ID in the calling agent's shell (CODEX_SESSION_ID is a fallback); obtain workDir from that shell's working directory. Do this yourself, without asking the user to find IDs. Never use an MCP transport ID, the server process environment, or the most recent chat as the target. If the host does not expose a reliable ID, report that limitation instead of guessing."

const serverInstructions = `Agent Scheduler delivers messages to existing agent sessions. When the user invokes agent-scheduler, use these MCP tools; do not substitute native scheduled tasks, automations, or confirmation cards. Scheduling only saves the message: do not execute it now or wait for delivery. Resolve the current chat ID yourself from the calling agent's shell: CODEX_THREAD_ID, falling back to CODEX_SESSION_ID. Pass it as sessionRef.` + contextInstructions + ` For another target session, use its explicitly identified reference. For Claude Code, use host-provided session context; do not reuse a Codex ID. Convert the requested time to RFC3339 using the current clock and the user's timezone. If expiry is unspecified, use 24 hours after dueAt. For one-time scheduling, call the scheduling tool once and stop on success. For persistent preference changes with set_auto_continue, always send a short final confirmation after success: “Persistent auto-continue is enabled for this chat.” or “Persistent auto-continue is disabled for this chat.” Status requests also require a readable final answer. Never return a blank final response for preference or status tools. If tools or context are unavailable, report the problem; do not silently create a native automation. Follow host permissions and approvals.`

type Server struct {
	store  *localstore.Store
	remote *controlplane.Client
}

type ScheduleMessageInput struct {
	Provider   string `json:"provider" jsonschema:"agent host: codex or claude-code"`
	SessionRef string `json:"sessionRef" jsonschema:"target session ID; for the current Codex chat read CODEX_THREAD_ID in the calling agent shell, falling back to CODEX_SESSION_ID; never sent to the scheduler service"`
	WorkDir    string `json:"workDir" jsonschema:"local absolute project directory for this agent session; never sent to the scheduler service"`
	Message    string `json:"message" jsonschema:"the exact message to deliver to this agent session"`
	DueAt      string `json:"dueAt" jsonschema:"RFC3339 timestamp when the message should be delivered"`
	ExpiresAt  string `json:"expiresAt" jsonschema:"RFC3339 timestamp after which the message must not be delivered"`
}

type ScheduleAutoContinueInput struct {
	SessionRef string `json:"sessionRef" jsonschema:"current Codex chat ID from the calling agent shell CODEX_THREAD_ID (fallback CODEX_SESSION_ID); never sent to the scheduler service"`
	WorkDir    string `json:"workDir" jsonschema:"local absolute project directory for this chat; never sent to the scheduler service"`
	Message    string `json:"message,omitempty" jsonschema:"optional exact instruction to deliver after the usage reset; omit to resume the interrupted task"`
}

type JobInput struct {
	Provider   string `json:"provider" jsonschema:"agent host: codex or claude-code"`
	SessionRef string `json:"sessionRef" jsonschema:"local agent session reference"`
	JobID      string `json:"jobId" jsonschema:"scheduled job ID returned when the job was created"`
}

type JobView struct {
	ID        string    `json:"id"`
	Provider  string    `json:"provider"`
	Message   string    `json:"message"`
	DueAt     time.Time `json:"dueAt"`
	ExpiresAt time.Time `json:"expiresAt"`
	State     string    `json:"state"`
}

type ScheduleResult struct {
	JobID            string    `json:"jobId"`
	DueAt            time.Time `json:"dueAt"`
	State            string    `json:"state"`
	ServerSyncQueued bool      `json:"serverSyncQueued"`
	Notice           string    `json:"notice,omitempty"`
}

type CancelResult struct {
	Cancelled        bool `json:"cancelled"`
	ServerSyncQueued bool `json:"serverSyncQueued"`
}

func New(store *localstore.Store, remote *controlplane.Client) (*Server, error) {
	if store == nil {
		return nil, fmt.Errorf("local store is required")
	}
	return &Server{store: store, remote: remote}, nil
}

func (s *Server) Run(ctx context.Context) error {
	server := mcp.NewServer(&mcp.Implementation{Name: "agent-scheduler", Version: "0.1.0"}, &mcp.ServerOptions{Instructions: serverInstructions})
	mcp.AddTool(server, &mcp.Tool{
		Name:        "schedule_message",
		Title:       "Schedule a message",
		Description: "Schedule up to three messages for this agent session. During this turn, only save the message; after success, stop without a separate confirmation or starting the message's requested work. The local connector retains the message and session reference and delivers it when due. It creates the opaque server scope ID locally. When agent-scheduler is requested, use this tool rather than native automations." + contextInstructions,
	}, s.scheduleMessage)
	mcp.AddTool(server, &mcp.Tool{
		Name:        "schedule_auto_continue",
		Title:       "Continue after usage reset",
		Description: "Read Codex usage locally, save the continuation message for one minute after the applicable reset, and stop. This call does not start the requested work or send message content to the scheduler service." + contextInstructions,
	}, s.scheduleAutoContinue)
	mcp.AddTool(server, &mcp.Tool{
		Name:        "list_scheduled_messages",
		Title:       "List scheduled messages",
		Description: "List this agent session's scheduled messages from local storage. Provide the provider and local session reference." + contextInstructions,
	}, s.listMessages)
	mcp.AddTool(server, &mcp.Tool{
		Name:        "cancel_scheduled_message",
		Title:       "Cancel a scheduled message",
		Description: "Cancel a message that has not begun delivery, or dismiss an uncertain delivery. A message already being sent to the agent cannot be retracted. Provide the provider and local session reference." + contextInstructions,
	}, s.cancelMessage)
	mcp.AddTool(server, &mcp.Tool{Name: "set_auto_continue", Description: "Enable or disable persistent auto-continue for future usage-limit failures in this Codex chat. Disable cancels pending watcher jobs. After success, explicitly confirm enabled or disabled in a short final reply." + contextInstructions}, s.setAutoContinue)
	mcp.AddTool(server, &mcp.Tool{Name: "get_auto_continue", Description: "Read persistent auto-continue status for this Codex chat." + contextInstructions}, s.getAutoContinue)
	mcp.AddTool(server, &mcp.Tool{Name: "record_model_request_outcome", Description: "Lifecycle hook callback only. Save a failed model-request hint for an enabled chat; this does not start work or confirm a terminal failure. Do not call manually."}, s.recordModelRequestOutcome)
	return server.Run(ctx, &mcp.StdioTransport{})
}

func (s *Server) scheduleAutoContinue(ctx context.Context, _ *mcp.CallToolRequest, in ScheduleAutoContinueInput) (*mcp.CallToolResult, ScheduleResult, error) {
	if !filepath.IsAbs(in.WorkDir) {
		return nil, ScheduleResult{}, fmt.Errorf("workDir must be an absolute local directory")
	}
	info, err := os.Stat(in.WorkDir)
	if err != nil || !info.IsDir() {
		return nil, ScheduleResult{}, fmt.Errorf("workDir must be an existing local directory")
	}
	due, err := usage.CodexReset(ctx, os.Getenv("AGENT_SCHEDULER_CODEX"), time.Now())
	if err != nil {
		return nil, ScheduleResult{}, err
	}
	message := in.Message
	if message == "" {
		message = "Resume the task that was stopped when the usage limit was reached in this chat. Continue from where it left off, preserving the user's instructions and ordinary permissions. If no work remains, report that briefly."
	}
	return s.saveMessage(ctx, jobs.ProviderCodex, in.SessionRef, in.WorkDir, message, due, due.Add(24*time.Hour))
}

func (s *Server) scheduleMessage(ctx context.Context, _ *mcp.CallToolRequest, in ScheduleMessageInput) (*mcp.CallToolResult, ScheduleResult, error) {
	if !filepath.IsAbs(in.WorkDir) {
		return nil, ScheduleResult{}, fmt.Errorf("workDir must be an absolute local directory")
	}
	info, err := os.Stat(in.WorkDir)
	if err != nil || !info.IsDir() {
		return nil, ScheduleResult{}, fmt.Errorf("workDir must be an existing local directory")
	}
	due, err := time.Parse(time.RFC3339, in.DueAt)
	if err != nil {
		return nil, ScheduleResult{}, fmt.Errorf("dueAt must be an RFC3339 timestamp")
	}
	expires, err := time.Parse(time.RFC3339, in.ExpiresAt)
	if err != nil {
		return nil, ScheduleResult{}, fmt.Errorf("expiresAt must be an RFC3339 timestamp")
	}
	return s.saveMessage(ctx, jobs.Provider(in.Provider), in.SessionRef, in.WorkDir, in.Message, due, expires)
}

func (s *Server) saveMessage(ctx context.Context, provider jobs.Provider, sessionRef, workDir, message string, due, expires time.Time) (*mcp.CallToolResult, ScheduleResult, error) {
	id, err := newID()
	if err != nil {
		return nil, ScheduleResult{}, err
	}
	scopeID, err := s.store.SessionScope(ctx, provider, sessionRef)
	if err != nil {
		return nil, ScheduleResult{}, err
	}
	state := jobs.StatePendingRegistration
	if s.remote == nil {
		state = jobs.StateScheduled
	}
	job := jobs.LocalJob{
		ID:             id,
		SessionScopeID: scopeID,
		Provider:       provider,
		SessionRef:     sessionRef,
		WorkDir:        workDir,
		Message:        message,
		DueAt:          due,
		ExpiresAt:      expires,
		State:          state,
		CreatedAt:      time.Now().UTC(),
	}
	if err := s.store.Create(ctx, job); err != nil {
		return nil, ScheduleResult{}, err
	}
	// Keep the scheduling turn short: save locally and return. The resident
	// connector worker syncs opaque metadata with the server in the background.
	notice := "Saved locally. The connector will sync delivery metadata in the background."
	if s.remote == nil {
		notice = "Saved locally. Cloud delivery is not configured on this connector."
	}
	return nil, ScheduleResult{
		JobID: job.ID, DueAt: due, State: string(jobs.StateScheduled),
		ServerSyncQueued: s.remote != nil,
		Notice:           notice,
	}, nil
}

func (s *Server) listMessages(ctx context.Context, _ *mcp.CallToolRequest, in struct {
	Provider   string `json:"provider" jsonschema:"agent host: codex or claude-code"`
	SessionRef string `json:"sessionRef" jsonschema:"local agent session reference"`
}) (*mcp.CallToolResult, []JobView, error) {
	scopeID, err := s.store.SessionScope(ctx, jobs.Provider(in.Provider), in.SessionRef)
	if err != nil {
		return nil, nil, err
	}
	items, err := s.store.List(ctx, scopeID)
	if err != nil {
		return nil, nil, err
	}
	views := make([]JobView, 0, len(items))
	for _, item := range items {
		views = append(views, JobView{
			ID: item.ID, Provider: string(item.Provider), Message: item.Message,
			DueAt: item.DueAt, ExpiresAt: item.ExpiresAt, State: string(item.State),
		})
	}
	return nil, views, nil
}

func (s *Server) cancelMessage(ctx context.Context, _ *mcp.CallToolRequest, in JobInput) (*mcp.CallToolResult, CancelResult, error) {
	scopeID, err := s.store.SessionScope(ctx, jobs.Provider(in.Provider), in.SessionRef)
	if err != nil {
		return nil, CancelResult{}, err
	}
	cancelled, err := s.store.Cancel(ctx, scopeID, in.JobID)
	if err != nil || !cancelled {
		return nil, CancelResult{Cancelled: false}, err
	}
	// Cancellation is local and immediate. If already registered, the worker
	// propagates it to the server during its next background sync.
	return nil, CancelResult{Cancelled: true, ServerSyncQueued: s.remote != nil}, nil
}

func newID() (string, error) {
	// UUIDv4 generated from the operating system CSPRNG.
	var id [16]byte
	if _, err := rand.Read(id[:]); err != nil {
		return "", fmt.Errorf("secure random source unavailable: %w", err)
	}
	id[6] = (id[6] & 0x0f) | 0x40
	id[8] = (id[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", id[0:4], id[4:6], id[6:8], id[8:10], id[10:16]), nil
}

type AutoContinueInput struct {
	SessionRef string `json:"sessionRef"`
	WorkDir    string `json:"workDir"`
	Enabled    bool   `json:"enabled"`
}

func (s *Server) setAutoContinue(ctx context.Context, _ *mcp.CallToolRequest, in AutoContinueInput) (*mcp.CallToolResult, localstore.AutoContinue, error) {
	a := localstore.AutoContinue{SessionRef: in.SessionRef, WorkDir: in.WorkDir, Enabled: in.Enabled}
	if in.Enabled {
		if !filepath.IsAbs(in.WorkDir) {
			return nil, a, fmt.Errorf("workDir must be absolute")
		}
		info, err := os.Stat(in.WorkDir)
		if err != nil || !info.IsDir() {
			return nil, a, fmt.Errorf("workDir must be an existing directory")
		}
		turn, err := usage.LatestTurn(ctx, "", in.SessionRef)
		if err != nil {
			return nil, a, fmt.Errorf("verify stored turn access: %w", err)
		}
		a.Baseline = turn.ID
	}
	if err := s.store.SetAutoContinue(ctx, a); err != nil {
		return nil, a, err
	}
	return s.getAutoContinue(ctx, nil, struct {
		SessionRef string `json:"sessionRef"`
	}{in.SessionRef})
}
func (s *Server) getAutoContinue(ctx context.Context, _ *mcp.CallToolRequest, in struct {
	SessionRef string `json:"sessionRef"`
}) (*mcp.CallToolResult, localstore.AutoContinue, error) {
	if in.SessionRef == "" {
		return nil, localstore.AutoContinue{}, fmt.Errorf("session reference is required")
	}
	settings, err := s.store.AutoContinueSettings(ctx)
	if err != nil {
		return nil, localstore.AutoContinue{}, err
	}
	for _, a := range settings {
		if a.SessionRef == in.SessionRef {
			return nil, a, nil
		}
	}
	return nil, localstore.AutoContinue{SessionRef: in.SessionRef}, nil
}

type ModelRequestOutcome struct {
	SessionRef string  `json:"sessionRef"`
	TurnID     string  `json:"turnId"`
	Status     string  `json:"status"`
	AgentID    *string `json:"agentId,omitempty"`
}
type HookResult struct {
	Accepted bool `json:"accepted"`
}

func (s *Server) recordModelRequestOutcome(ctx context.Context, _ *mcp.CallToolRequest, in ModelRequestOutcome) (*mcp.CallToolResult, HookResult, error) {
	if in.Status != "failed" {
		return nil, HookResult{}, nil
	}
	if in.SessionRef == "" || in.TurnID == "" {
		return nil, HookResult{}, fmt.Errorf("hook session and turn IDs are required")
	}
	// Subagent hooks may carry the parent's session_id. Never attribute them to it.
	if in.AgentID != nil && *in.AgentID != "" && *in.AgentID != in.SessionRef {
		return nil, HookResult{}, nil
	}
	if err := s.store.RecordFailureEvent(ctx, in.SessionRef, in.TurnID); err != nil {
		return nil, HookResult{}, err
	}
	return nil, HookResult{Accepted: true}, nil
}
