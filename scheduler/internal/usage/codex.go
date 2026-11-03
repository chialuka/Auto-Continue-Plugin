// Package usage reads Codex account limits through the local Codex app-server.
package usage

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"
)

const readTimeout = 15 * time.Second

type rateLimits struct {
	Buckets map[string]bucket `json:"rateLimitsByLimitId"`
}

type bucket struct {
	Primary   *window `json:"primary"`
	Secondary *window `json:"secondary"`
}

type window struct {
	UsedPercent float64 `json:"usedPercent"`
	ResetsAt    float64 `json:"resetsAt"`
}

type response struct {
	ID     int             `json:"id"`
	Result json.RawMessage `json:"result"`
	Error  json.RawMessage `json:"error"`
}

// CodexReset reads usage without starting a model turn and returns the next
// safe resume time, one minute after the relevant usage reset.
func CodexReset(parent context.Context, executable string, now time.Time) (time.Time, error) {
	data, err := codexRead(parent, executable, "account/rateLimits/read", map[string]any{})
	if err != nil {
		return time.Time{}, err
	}
	var limits rateLimits
	if err := json.Unmarshal(data, &limits); err != nil {
		return time.Time{}, fmt.Errorf("decode Codex account limits: %w", err)
	}
	key := "codex"
	if _, ok := limits.Buckets[key]; !ok {
		if len(limits.Buckets) != 1 {
			return time.Time{}, errors.New("Codex usage did not identify one applicable limit bucket")
		}
		for key = range limits.Buckets {
		}
	}
	return resetForBucket(limits.Buckets[key], now)
}

func resetForBucket(selected bucket, now time.Time) (time.Time, error) {
	windows := []*window{selected.Primary, selected.Secondary}
	var exhausted, available []time.Time
	for _, item := range windows {
		if item == nil {
			continue
		}
		if math.IsNaN(item.ResetsAt) || math.IsInf(item.ResetsAt, 0) || item.ResetsAt <= float64(now.Unix()) {
			if item.UsedPercent >= 100 {
				return time.Time{}, errors.New("Codex reported an exhausted window without a reliable future reset time")
			}
			continue
		}
		reset := time.Unix(int64(math.Ceil(item.ResetsAt)), 0)
		available = append(available, reset)
		if item.UsedPercent >= 100 {
			exhausted = append(exhausted, reset)
		}
	}
	selectedResets := available
	if len(exhausted) != 0 {
		selectedResets = exhausted
	}
	if len(selectedResets) == 0 {
		return time.Time{}, errors.New("Codex did not return a reliable future reset time")
	}
	reset := selectedResets[0]
	for _, candidate := range selectedResets[1:] {
		if (len(exhausted) != 0 && candidate.After(reset)) || (len(exhausted) == 0 && candidate.Before(reset)) {
			reset = candidate
		}
	}
	return reset.Add(time.Minute), nil
}

func send(w io.Writer, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if _, err := w.Write(append(data, '\n')); err != nil {
		return fmt.Errorf("write Codex app-server request: %w", err)
	}
	return nil
}

func readResult(ctx context.Context, reader *bufio.Reader, id int) (json.RawMessage, error) {
	for {
		if err := ctx.Err(); err != nil {
			return nil, fmt.Errorf("Codex app-server timed out: %w", err)
		}
		line, err := reader.ReadBytes('\n')
		if err != nil {
			return nil, fmt.Errorf("read Codex app-server response: %w", err)
		}
		var item response
		if err := json.Unmarshal(bytes.TrimSpace(line), &item); err != nil {
			continue
		}
		if item.ID != id {
			continue
		}
		if len(item.Error) != 0 && string(item.Error) != "null" {
			return nil, fmt.Errorf("Codex app-server returned an error: %s", item.Error)
		}
		return item.Result, nil
	}
}

// LatestTurn reads persisted history without loading or starting the thread.
type Turn struct {
	ID     string `json:"id"`
	Status string `json:"status"`
	Error  *struct {
		Code json.RawMessage `json:"codexErrorInfo"`
	} `json:"error"`
}

func (t Turn) UsageBlocked() bool {
	return t.ID != "" && t.Status == "failed" && t.Error != nil && string(t.Error.Code) == `"usageLimitExceeded"`
}
func LatestTurn(ctx context.Context, executable, thread string) (Turn, error) {
	data, err := codexRead(ctx, executable, "thread/turns/list", map[string]any{"threadId": thread, "limit": 1, "sortDirection": "desc", "itemsView": "notLoaded"})
	if err != nil {
		return Turn{}, err
	}
	var result struct {
		Data []Turn `json:"data"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return Turn{}, err
	}
	if len(result.Data) == 0 {
		return Turn{}, nil
	}
	return result.Data[0], nil
}
func codexRead(parent context.Context, executable, method string, params map[string]any) (json.RawMessage, error) {
	if executable == "" {
		executable = os.Getenv("AGENT_SCHEDULER_CODEX")
	}
	if executable == "" {
		executable, _ = exec.LookPath("codex")
	}
	if executable == "" && runtime.GOOS == "darwin" {
		candidate := "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			executable = filepath.Clean(candidate)
		}
	}
	if executable == "" {
		return nil, errors.New("Codex CLI is unavailable; install it or set AGENT_SCHEDULER_CODEX")
	}
	ctx, cancel := context.WithTimeout(parent, readTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, executable, "app-server", "--listen", "stdio://")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("start Codex usage reader: %w", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("read Codex usage reader: %w", err)
	}
	cmd.Stderr = io.Discard
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start Codex app-server: %w", err)
	}
	defer func() {
		_ = stdin.Close()
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}()

	reader := bufio.NewReader(stdout)
	if err := send(stdin, map[string]any{
		"id": 1, "method": "initialize", "params": map[string]any{
			"clientInfo": map[string]string{"name": "agent-scheduler", "version": "0.1.0"},
		},
	}); err != nil {
		return nil, err
	}
	if _, err := readResult(ctx, reader, 1); err != nil {
		return nil, fmt.Errorf("initialize Codex app-server: %w", err)
	}
	if err := send(stdin, map[string]any{"method": "initialized"}); err != nil {
		return nil, err
	}
	if err := send(stdin, map[string]any{"id": 2, "method": method, "params": params}); err != nil {
		return nil, err
	}
	data, err := readResult(ctx, reader, 2)
	if err != nil {
		return nil, fmt.Errorf("read Codex account limits: %w", err)
	}
	return data, nil
}
