// The library's command line: a client of the service's HTTP JSON API (see client/client.go).
package main

import (
	"fmt"
	"os"

	"library-lending/artifacts/cli/client"
)

func main() {
	command, err := client.Parse(os.Args[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "library: %v\n", err)
		os.Exit(2)
	}
	os.Exit(client.Run(command, client.New(command.Service), os.Stdout, os.Stderr))
}
