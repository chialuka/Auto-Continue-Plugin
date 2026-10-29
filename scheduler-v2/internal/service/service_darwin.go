//go:build darwin

package service

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const label = "com.agent-scheduler.connector"

func Install(executable string) error {
	configDir, err := os.UserConfigDir()
	if err != nil {
		return err
	}
	logDir := filepath.Join(configDir, "agent-scheduler", "logs")
	if err := os.MkdirAll(logDir, 0o700); err != nil {
		return err
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	launchAgents := filepath.Join(home, "Library", "LaunchAgents")
	if err := os.MkdirAll(launchAgents, 0o700); err != nil {
		return err
	}
	plist := filepath.Join(launchAgents, label+".plist")
	contents := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>%s</string>
<key>ProgramArguments</key><array><string>%s</string><string>daemon</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>%s</string>
<key>StandardErrorPath</key><string>%s</string>
</dict></plist>
`, label, xmlEscape(executable), xmlEscape(filepath.Join(logDir, "connector.log")), xmlEscape(filepath.Join(logDir, "connector-error.log")))
	if err := os.WriteFile(plist, []byte(contents), 0o600); err != nil {
		return err
	}
	if err := os.Chmod(plist, 0o600); err != nil {
		return err
	}
	domain := fmt.Sprintf("gui/%d", os.Getuid())
	_ = exec.Command("launchctl", "bootout", domain, plist).Run()
	if output, err := exec.Command("launchctl", "bootstrap", domain, plist).CombinedOutput(); err != nil {
		return fmt.Errorf("start launch agent: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func Uninstall() error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	launchAgents := filepath.Join(home, "Library", "LaunchAgents")
	plist := filepath.Join(launchAgents, label+".plist")
	domain := fmt.Sprintf("gui/%d", os.Getuid())
	_ = exec.Command("launchctl", "bootout", domain, plist).Run()
	if err := os.Remove(plist); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func Stop() error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	plist := filepath.Join(home, "Library", "LaunchAgents", label+".plist")
	domain := fmt.Sprintf("gui/%d", os.Getuid())
	output, err := exec.Command("launchctl", "bootout", domain, plist).CombinedOutput()
	if err != nil && !strings.Contains(strings.ToLower(string(output)), "could not find") &&
		!strings.Contains(strings.ToLower(string(output)), "not found") {
		return fmt.Errorf("stop connector service: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func xmlEscape(value string) string {
	value = strings.ReplaceAll(value, "&", "&amp;")
	value = strings.ReplaceAll(value, "<", "&lt;")
	value = strings.ReplaceAll(value, ">", "&gt;")
	value = strings.ReplaceAll(value, "\"", "&quot;")
	return strings.ReplaceAll(value, "'", "&apos;")
}
