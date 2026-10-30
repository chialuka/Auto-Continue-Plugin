// Package host delivers local job messages through installed agent CLIs.
package host

import (
	"context"
	"fmt"
	"os/exec"
	"time"

	"agent-scheduler/internal/jobs"
)

type Dispatcher interface {
	Dispatch(context.Context, jobs.LocalJob) error
}

// CLI starts a new host process to deliver a message to the saved session.
// It passes content as a separate argument and never constructs a shell command.
type CLI struct {
	CodexPath  string
	ClaudePath string
}

func (c CLI) Dispatch(parent context.Context, job jobs.LocalJob) error {
	ctx, cancel := context.WithTimeout(parent, 3*time.Minute)
	defer cancel()
	var cmd *exec.Cmd
	switch job.Provider {
	case jobs.ProviderCodex:
		path := c.CodexPath
		if path == "" {
			path = "codex"
		}
		cmd = exec.CommandContext(ctx, path, "queue", "--thread", job.SessionRef, "--message", job.Message)
	case jobs.ProviderClaudeCode:
		path := c.ClaudePath
		if path == "" {
			path = "claude"
		}
		cmd = exec.CommandContext(ctx, path, "--resume", job.SessionRef, "--print", job.Message)
	default:
		return fmt.Errorf("unsupported agent provider %q", job.Provider)
	}
	cmd.Dir = job.WorkDir
	output, err := cmd.CombinedOutput()
	if err != nil {
		if ctx.Err() != nil {
			return fmt.Errorf("agent delivery timed out: %w", ctx.Err())
		}
		// Keep failure logs compact; the host may include user project content.
		if len(output) > 512 {
			output = output[:512]
		}
		return fmt.Errorf("agent CLI delivery failed: %w: %s", err, string(output))
	}
	return nil
}
