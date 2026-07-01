// cmd/diogenes/main.go
//
// The Diogenes Go CLI.
//
// Currently a stub — the Python CLI (sdk/python/src/diogenes/cli.py) is the
// active implementation. This binary will replace it once the Go port is built.
//
// Build:   mise //cli:build
// Install: go install github.com/meaganewaller/diogenes/cli/cmd/diogenes@latest

package main

import (
	"fmt"
	"os"
)

var version = "dev" // injected at build time via -ldflags

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--version" {
		fmt.Printf("diogenes %s\n", version)
		os.Exit(0)
	}

	fmt.Fprintln(os.Stderr, "diogenes Go CLI: not yet implemented.")
	fmt.Fprintln(os.Stderr, "Use the Python CLI for now: pip install diogenes-sdk")
	os.Exit(1)
}