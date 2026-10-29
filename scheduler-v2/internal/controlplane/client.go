// Package controlplane contains the device-to-scheduler HTTPS client.
package controlplane

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"agent-scheduler/internal/jobs"
)

const maxResponseBytes = 1 << 20

type Client struct {
	baseURL     string
	deviceToken string
	httpClient  *http.Client
}

type Credentials struct {
	UserID      string `json:"userId"`
	DeviceID    string `json:"deviceId"`
	DeviceToken string `json:"deviceToken"`
}

type DeviceSignal struct {
	Type    string    `json:"type"`
	JobID   string    `json:"jobId"`
	Attempt int       `json:"attempt"`
	DueAt   time.Time `json:"dueAt"`
}

func Enroll(ctx context.Context, baseURL string) (Credentials, error) {
	baseURL, err := validateURL(baseURL)
	if err != nil {
		return Credentials{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL+"/v1/enroll", bytes.NewReader([]byte("{}")))
	if err != nil {
		return Credentials{}, fmt.Errorf("build enrollment request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: 15 * time.Second}).Do(request)
	if err != nil {
		return Credentials{}, fmt.Errorf("scheduler enrollment failed: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return Credentials{}, fmt.Errorf("scheduler enrollment returned HTTP %d", response.StatusCode)
	}
	var credentials Credentials
	if err := json.NewDecoder(io.LimitReader(response.Body, maxResponseBytes)).Decode(&credentials); err != nil {
		return Credentials{}, fmt.Errorf("decode scheduler enrollment: %w", err)
	}
	if strings.TrimSpace(credentials.DeviceID) == "" || strings.TrimSpace(credentials.DeviceToken) == "" {
		return Credentials{}, fmt.Errorf("scheduler returned incomplete enrollment credentials")
	}
	return credentials, nil
}

func New(baseURL, deviceToken string) (*Client, error) {
	baseURL, err := validateURL(baseURL)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(deviceToken) == "" {
		return nil, fmt.Errorf("device token is required")
	}
	return &Client{
		baseURL:     baseURL,
		deviceToken: deviceToken,
		httpClient:  &http.Client{Timeout: 15 * time.Second},
	}, nil
}

func validateURL(baseURL string) (string, error) {
	baseURL = strings.TrimRight(baseURL, "/")
	parsed, err := url.Parse(baseURL)
	if err != nil || (parsed.Scheme != "https" && parsed.Scheme != "http") || parsed.Host == "" {
		return "", fmt.Errorf("invalid scheduler URL")
	}
	if parsed.Scheme != "https" && parsed.Host != "localhost" && !strings.HasPrefix(parsed.Host, "127.0.0.1:") {
		return "", fmt.Errorf("scheduler connections must use HTTPS")
	}
	return baseURL, nil
}

func (c *Client) CreateJob(ctx context.Context, job jobs.ServerJob) (jobs.ServerJob, error) {
	if err := job.Validate(); err != nil {
		return jobs.ServerJob{}, err
	}
	var saved jobs.ServerJob
	request := struct {
		ID             string    `json:"id"`
		SessionScopeID string    `json:"sessionScopeId"`
		DueAt          time.Time `json:"dueAt"`
		ExpiresAt      time.Time `json:"expiresAt"`
	}{job.ID, job.SessionScopeID, job.DueAt, job.ExpiresAt}
	err := c.do(ctx, http.MethodPost, "/v1/jobs", request, &saved)
	return saved, err
}

func (c *Client) ListJobs(ctx context.Context, scopeID string) ([]jobs.ServerJob, error) {
	path := "/v1/jobs?sessionScopeId=" + url.QueryEscape(scopeID)
	var result []jobs.ServerJob
	err := c.do(ctx, http.MethodGet, path, nil, &result)
	return result, err
}

func (c *Client) CancelJob(ctx context.Context, id string) error {
	return c.do(ctx, http.MethodDelete, "/v1/jobs/"+url.PathEscape(id), nil, nil)
}

func (c *Client) AckJob(ctx context.Context, id string, attempt int, outcome string) error {
	if attempt < 0 || attempt > jobs.MaxDeliveryRetries {
		return fmt.Errorf("attempt must be between 0 and %d", jobs.MaxDeliveryRetries)
	}
	switch outcome {
	case "acknowledged", "connection_unavailable", "timeout", "rejected", "uncertain":
	default:
		return fmt.Errorf("invalid delivery outcome %q", outcome)
	}
	payload := struct {
		Attempt int    `json:"attempt"`
		Outcome string `json:"outcome"`
	}{Attempt: attempt, Outcome: outcome}
	return c.do(ctx, http.MethodPost, "/v1/jobs/"+url.PathEscape(id)+"/ack", payload, nil)
}

func (c *Client) Stream(ctx context.Context, receive func(context.Context, DeviceSignal)) error {
	endpoint, err := url.Parse(c.baseURL)
	if err != nil {
		return fmt.Errorf("parse scheduler URL: %w", err)
	}
	if endpoint.Scheme == "https" {
		endpoint.Scheme = "wss"
	} else {
		endpoint.Scheme = "ws"
	}
	endpoint.Path = strings.TrimRight(endpoint.Path, "/") + "/v1/device/stream"
	endpoint.RawQuery = ""
	header := make(http.Header)
	header.Set("Authorization", "Bearer "+c.deviceToken)
	conn, _, err := websocket.Dial(ctx, endpoint.String(), &websocket.DialOptions{HTTPHeader: header})
	if err != nil {
		return fmt.Errorf("connect scheduler device stream: %w", err)
	}
	defer conn.Close(websocket.StatusNormalClosure, "connector stopped")
	conn.SetReadLimit(8 * 1024)

	streamCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-streamCtx.Done():
				return
			case <-ticker.C:
				pingCtx, pingCancel := context.WithTimeout(streamCtx, 10*time.Second)
				err := conn.Ping(pingCtx)
				pingCancel()
				if err != nil {
					cancel()
					return
				}
			}
		}
	}()

	for {
		var signal DeviceSignal
		if err := wsjson.Read(streamCtx, conn, &signal); err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return fmt.Errorf("read scheduler device signal: %w", err)
		}
		if signal.Type != "job_due" || signal.JobID == "" || signal.Attempt < 0 || signal.Attempt > jobs.MaxDeliveryRetries || signal.DueAt.IsZero() {
			continue
		}
		receive(streamCtx, signal)
	}
}

func (c *Client) do(ctx context.Context, method, path string, input, output any) error {
	var body io.Reader
	if input != nil {
		encoded, err := json.Marshal(input)
		if err != nil {
			return fmt.Errorf("encode scheduler request: %w", err)
		}
		body = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, body)
	if err != nil {
		return fmt.Errorf("build scheduler request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+c.deviceToken)
	if input != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("scheduler request failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("scheduler returned HTTP %d", resp.StatusCode)
	}
	if output == nil {
		return nil
	}
	decoder := json.NewDecoder(io.LimitReader(resp.Body, maxResponseBytes))
	if err := decoder.Decode(output); err != nil {
		return fmt.Errorf("decode scheduler response: %w", err)
	}
	return nil
}
