//go:build !darwin && !linux && !windows

package service

func Install(string) error { return Unsupported() }

func Uninstall() error { return Unsupported() }

func Stop() error { return Unsupported() }
