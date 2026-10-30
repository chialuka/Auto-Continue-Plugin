// Package config stores the server address and the device token on the user's device.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

type Settings struct {
	SchedulerURL string `json:"schedulerUrl"`
	UserID       string `json:"userId"`
	DeviceID     string `json:"deviceId"`
	DeviceToken  string `json:"deviceToken"`
}

func Path() (string, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", fmt.Errorf("find user configuration directory: %w", err)
	}
	return filepath.Join(dir, "agent-scheduler", "config.json"), nil
}

func Load() (Settings, error) {
	path, err := Path()
	if err != nil {
		return Settings{}, err
	}
	contents, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return Settings{}, nil
		}
		return Settings{}, fmt.Errorf("read scheduler configuration: %w", err)
	}
	var settings Settings
	if err := json.Unmarshal(contents, &settings); err != nil {
		return Settings{}, fmt.Errorf("parse scheduler configuration: %w", err)
	}
	return settings, nil
}

func Save(settings Settings) error {
	path, err := Path()
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("create scheduler configuration directory: %w", err)
	}
	contents, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return fmt.Errorf("encode scheduler configuration: %w", err)
	}
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, contents, 0o600); err != nil {
		return fmt.Errorf("write scheduler configuration: %w", err)
	}
	if err := os.Chmod(temporary, 0o600); err != nil {
		_ = os.Remove(temporary)
		return fmt.Errorf("restrict scheduler configuration permissions: %w", err)
	}
	if err := os.Rename(temporary, path); err != nil {
		_ = os.Remove(temporary)
		return fmt.Errorf("save scheduler configuration: %w", err)
	}
	return nil
}
