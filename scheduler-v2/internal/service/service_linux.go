//go:build linux

package service

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const unitName = "agent-scheduler.service"

func Install(executable string) error {
	configDir, err := os.UserConfigDir()
	if err != nil {
		return err
	}
	unitDir := filepath.Join(configDir, "systemd", "user")
	if err := os.MkdirAll(unitDir, 0o700); err != nil {
		return err
	}
	unit := fmt.Sprintf(`[Unit]
Description=Agent Scheduler Connector
After=network-online.target

[Service]
ExecStart=%s daemon
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
`, systemdEscape(executable))
	path := filepath.Join(unitDir, unitName)
	if err := os.WriteFile(path, []byte(unit), 0o600); err != nil {
		return err
	}
	if output, err := exec.Command("systemctl", "--user", "daemon-reload").CombinedOutput(); err != nil {
		return fmt.Errorf("reload user systemd units: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if output, err := exec.Command("systemctl", "--user", "enable", "--now", unitName).CombinedOutput(); err != nil {
		return fmt.Errorf("enable connector service: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func Uninstall() error {
	if output, err := exec.Command("systemctl", "--user", "disable", "--now", unitName).CombinedOutput(); err != nil {
		if !strings.Contains(string(output), "not loaded") && !strings.Contains(string(output), "not found") {
			return fmt.Errorf("stop connector service: %w: %s", err, strings.TrimSpace(string(output)))
		}
	}
	configDir, err := os.UserConfigDir()
	if err != nil {
		return err
	}
	path := filepath.Join(configDir, "systemd", "user", unitName)
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return err
	}
	return exec.Command("systemctl", "--user", "daemon-reload").Run()
}

func Stop() error {
	if output, err := exec.Command("systemctl", "--user", "stop", unitName).CombinedOutput(); err != nil {
		lower := strings.ToLower(string(output))
		if !strings.Contains(lower, "not loaded") && !strings.Contains(lower, "not found") && !strings.Contains(lower, "not running") {
			return fmt.Errorf("stop connector service: %w: %s", err, strings.TrimSpace(string(output)))
		}
	}
	return nil
}

func systemdEscape(value string) string {
	value = strings.ReplaceAll(value, `\`, `\\`)
	value = strings.ReplaceAll(value, `"`, `\"`)
	value = strings.ReplaceAll(value, " ", `\s`)
	return `"` + value + `"`
}
