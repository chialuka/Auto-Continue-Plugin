package main

import (
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"agent-scheduler/internal/config"
	"agent-scheduler/internal/controlplane"
	"agent-scheduler/internal/host"
	"agent-scheduler/internal/localstore"
	"agent-scheduler/internal/mcpserver"
	"agent-scheduler/internal/service"
	"agent-scheduler/internal/worker"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	var err error
	switch {
	case len(os.Args) > 1 && os.Args[1] == "setup":
		err = setup(ctx, os.Args[2:])
	case len(os.Args) > 1 && os.Args[1] == "install-service":
		err = installService()
	case len(os.Args) > 1 && os.Args[1] == "uninstall-service":
		err = service.Uninstall()
	case len(os.Args) > 1 && os.Args[1] == "daemon":
		err = runDaemon(ctx)
	default:
		err = runMCP(ctx)
	}
	if err != nil {
		log.Printf("agent-scheduler: %v", err)
		os.Exit(1)
	}
}

func runMCP(ctx context.Context) error {
	store, remote, err := openResources(ctx)
	if err != nil {
		return err
	}
	defer store.Close()
	server, err := mcpserver.New(store, remote)
	if err != nil {
		return err
	}
	connectorWorker := newWorker(store, remote)
	go connectorWorker.Run(ctx)
	go connectorWorker.RunStream(ctx)
	return server.Run(ctx)
}

func runDaemon(ctx context.Context) error {
	store, remote, err := openResources(ctx)
	if err != nil {
		return err
	}
	defer store.Close()
	connectorWorker := newWorker(store, remote)
	go connectorWorker.Run(ctx)
	go connectorWorker.RunStream(ctx)
	<-ctx.Done()
	return nil
}

func openResources(ctx context.Context) (*localstore.Store, *controlplane.Client, error) {
	dataDir, err := os.UserConfigDir()
	if err != nil {
		return nil, nil, err
	}
	dataDir = filepath.Join(dataDir, "agent-scheduler")
	dbPath := os.Getenv("AGENT_SCHEDULER_DB")
	if dbPath == "" {
		dbPath = filepath.Join(dataDir, "jobs.sqlite3")
	}
	store, err := localstore.Open(dbPath)
	if err != nil {
		return nil, nil, err
	}

	var remote *controlplane.Client
	settings, err := config.Load()
	if err != nil {
		_ = store.Close()
		return nil, nil, err
	}
	baseURL := os.Getenv("AGENT_SCHEDULER_URL")
	if baseURL == "" {
		baseURL = settings.SchedulerURL
	}
	token := os.Getenv("AGENT_SCHEDULER_DEVICE_TOKEN")
	if token == "" && baseURL != "" {
		if settings.SchedulerURL == baseURL && settings.DeviceToken != "" {
			token = settings.DeviceToken
		} else {
			credentials, err := controlplane.Enroll(ctx, baseURL)
			if err != nil {
				_ = store.Close()
				return nil, nil, err
			}
			settings = config.Settings{
				SchedulerURL: baseURL,
				UserID:       credentials.UserID,
				DeviceID:     credentials.DeviceID,
				DeviceToken:  credentials.DeviceToken,
			}
			if err := config.Save(settings); err != nil {
				_ = store.Close()
				return nil, nil, err
			}
			token = credentials.DeviceToken
		}
	}
	if baseURL != "" || token != "" {
		remote, err = controlplane.New(baseURL, token)
		if err != nil {
			_ = store.Close()
			return nil, nil, err
		}
	}
	return store, remote, nil
}

func newWorker(store *localstore.Store, remote *controlplane.Client) worker.Worker {
	return worker.Worker{
		Store:  store,
		Remote: remote,
		Dispatcher: host.CLI{
			CodexPath: os.Getenv("AGENT_SCHEDULER_CODEX"), ClaudePath: os.Getenv("AGENT_SCHEDULER_CLAUDE"),
		},
	}
}

func setup(ctx context.Context, args []string) error {
	var serverURL string
	configureAgents := true
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--url":
			i++
			if i >= len(args) {
				return fmt.Errorf("--url requires a value")
			}
			serverURL = strings.TrimSpace(args[i])
		case "--skip-agent-config":
			configureAgents = false
		default:
			return fmt.Errorf("usage: agent-scheduler setup [--url https://scheduler.example] [--skip-agent-config]")
		}
	}
	serverURL = strings.TrimRight(serverURL, "/")
	settings, err := config.Load()
	if err != nil {
		return err
	}
	if serverURL != "" && (settings.SchedulerURL != serverURL || settings.DeviceToken == "") {
		credentials, err := controlplane.Enroll(ctx, serverURL)
		if err != nil {
			return err
		}
		settings = config.Settings{
			SchedulerURL: serverURL,
			UserID:       credentials.UserID,
			DeviceID:     credentials.DeviceID,
			DeviceToken:  credentials.DeviceToken,
		}
		if err := config.Save(settings); err != nil {
			return err
		}
	}
	if serverURL != "" {
		fmt.Printf("Scheduler device ready: %s\n", settings.DeviceID)
	} else if settings.SchedulerURL != "" {
		fmt.Println("Using the existing cloud configuration. Run local-only setup with an unconfigured profile for local delivery.")
	} else {
		fmt.Println("Using local-only scheduling.")
	}
	executable, err := installBinary()
	if err != nil {
		return err
	}
	if err := service.Install(executable); err != nil {
		return err
	}
	configured := false
	if configureAgents {
		configured, err = configureAgentCLIs(executable)
		if err != nil {
			return err
		}
	}
	if configured {
		fmt.Println("Setup complete. Restart the configured agent app to load its MCP server.")
	} else {
		fmt.Println("Setup complete. Add this executable as an MCP server in your agent app to schedule messages.")
	}
	return nil
}

func installService() error {
	executable, err := installBinary()
	if err != nil {
		return err
	}
	return service.Install(executable)
}

func installBinary() (string, error) {
	current, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("find connector executable: %w", err)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("find user home directory: %w", err)
	}
	name := "agent-scheduler"
	if filepath.Ext(current) == ".exe" {
		name += ".exe"
	}
	installDir := filepath.Join(home, ".local", "bin")
	if err := os.MkdirAll(installDir, 0o700); err != nil {
		return "", fmt.Errorf("create connector install directory: %w", err)
	}
	destination := filepath.Join(installDir, name)
	currentAbs, _ := filepath.Abs(current)
	destinationAbs, _ := filepath.Abs(destination)
	if currentAbs == destinationAbs {
		return destination, nil
	}
	if same, err := sameFileContents(current, destination); err != nil {
		return "", err
	} else if same {
		return destination, nil
	} else if _, err := os.Stat(destination); err == nil {
		if err := service.Stop(); err != nil {
			return "", err
		}
	}
	source, err := os.Open(current)
	if err != nil {
		return "", fmt.Errorf("open connector executable: %w", err)
	}
	defer source.Close()
	temporary := destination + ".new"
	target, err := os.OpenFile(temporary, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o700)
	if err != nil {
		return "", fmt.Errorf("create installed connector: %w", err)
	}
	if _, err := io.Copy(target, source); err != nil {
		_ = target.Close()
		_ = os.Remove(temporary)
		return "", fmt.Errorf("copy connector executable: %w", err)
	}
	if err := target.Close(); err != nil {
		_ = os.Remove(temporary)
		return "", fmt.Errorf("finish installed connector: %w", err)
	}
	if err := os.Chmod(temporary, 0o700); err != nil {
		_ = os.Remove(temporary)
		return "", fmt.Errorf("set connector permissions: %w", err)
	}
	if err := os.Remove(destination); err != nil && !os.IsNotExist(err) {
		_ = os.Remove(temporary)
		return "", fmt.Errorf("replace installed connector: %w", err)
	}
	if err := os.Rename(temporary, destination); err != nil {
		_ = os.Remove(temporary)
		return "", fmt.Errorf("activate installed connector: %w", err)
	}
	return destination, nil
}

func sameFileContents(first, second string) (bool, error) {
	firstFile, err := os.Open(first)
	if err != nil {
		return false, fmt.Errorf("open connector executable: %w", err)
	}
	defer firstFile.Close()
	secondFile, err := os.Open(second)
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("open installed connector: %w", err)
	}
	defer secondFile.Close()
	firstHash, err := io.ReadAll(firstFile)
	if err != nil {
		return false, fmt.Errorf("read connector executable: %w", err)
	}
	secondHash, err := io.ReadAll(secondFile)
	if err != nil {
		return false, fmt.Errorf("read installed connector: %w", err)
	}
	return sha256.Sum256(firstHash) == sha256.Sum256(secondHash), nil
}

func configureAgentCLIs(executable string) (bool, error) {
	configured := false
	if _, err := exec.LookPath("codex"); err == nil {
		if err := configureAgentCLI("codex", executable,
			[]string{"mcp", "list"}, []string{"mcp", "add", "agent-scheduler", "--", executable}); err != nil {
			return configured, err
		}
		configured = true
	}
	if _, err := exec.LookPath("claude"); err == nil {
		if err := configureAgentCLI("claude", executable,
			[]string{"mcp", "list"}, []string{"mcp", "add", "--scope", "user", "--transport", "stdio", "agent-scheduler", "--", executable}); err != nil {
			return configured, err
		}
		configured = true
	}
	return configured, nil
}

func configureAgentCLI(name, executable string, listArgs, addArgs []string) error {
	listed, err := exec.Command(name, listArgs...).CombinedOutput()
	if err == nil && hasServerName(string(listed), "agent-scheduler") {
		fmt.Printf("%s already has an agent-scheduler MCP entry; leaving it unchanged.\n", name)
		return nil
	}
	output, err := exec.Command(name, addArgs...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("configure %s MCP server: %w: %s", name, err, strings.TrimSpace(string(output)))
	}
	fmt.Printf("Configured agent-scheduler MCP in %s.\n", name)
	return nil
}

func hasServerName(listing, name string) bool {
	for _, line := range strings.Split(listing, "\n") {
		for _, field := range strings.Fields(line) {
			field = strings.Trim(field, ":*`'\"()")
			if field == name {
				return true
			}
		}
	}
	return false
}
