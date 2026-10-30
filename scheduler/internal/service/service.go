// Package service installs the connector daemon as a per-user login service.
package service

import "fmt"

func Unsupported() error {
	return fmt.Errorf("user service installation is unsupported on this operating system")
}
