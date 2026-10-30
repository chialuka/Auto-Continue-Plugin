//go:build windows

package service

import (
	"fmt"
	"os/exec"
	"strings"
)

const taskName = "AgentSchedulerConnector"

func Install(executable string) error {
	trigger := fmt.Sprintf(`"%s" daemon`, executable)
	if output, err := exec.Command("schtasks.exe", "/Create", "/SC", "ONLOGON", "/TN", taskName,
		"/TR", trigger, "/RL", "LIMITED", "/F").CombinedOutput(); err != nil {
		return fmt.Errorf("create user startup task: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if output, err := exec.Command("schtasks.exe", "/Run", "/TN", taskName).CombinedOutput(); err != nil {
		return fmt.Errorf("start connector task: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func Uninstall() error {
	output, err := exec.Command("schtasks.exe", "/Delete", "/TN", taskName, "/F").CombinedOutput()
	if err != nil && !strings.Contains(string(output), "cannot find") {
		return fmt.Errorf("remove user startup task: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func Stop() error {
	output, err := exec.Command("schtasks.exe", "/End", "/TN", taskName).CombinedOutput()
	if err != nil && !strings.Contains(strings.ToLower(string(output)), "not running") {
		return fmt.Errorf("stop connector task: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}
