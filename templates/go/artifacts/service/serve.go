// Run the library-lending service.
//
//	go run ./artifacts/service [--host 127.0.0.1] [--port 8080] [--today 2026-10-01] [--verbose]
//
// The state is in memory and goes when the process does. --today pins the server's clock to
// one date, so a demonstration or a book chapter gives the same due dates on any day it runs;
// without it the server dates each request with today, counted in UTC days.
//
// The server runs until the process is interrupted; only a refusal exits here.
package main

import (
	"fmt"
	"os"
	"os/signal"
	"strconv"

	"library-lending/artifacts/service/lending"
)

const usage = "usage: serve [--host HOST] [--port PORT] [--today YYYY-MM-DD] [--verbose]"

type args struct {
	host    string
	port    int
	today   *lending.Day
	verbose bool
}

func parse(argv []string) (args, error) {
	out := args{host: "127.0.0.1", port: 8080}
	for at := 0; at < len(argv); at++ {
		flag := argv[at]
		value := func() (string, error) {
			at++
			if at >= len(argv) {
				return "", fmt.Errorf("%s needs a value", flag)
			}
			return argv[at], nil
		}
		switch flag {
		case "--host":
			v, err := value()
			if err != nil {
				return out, err
			}
			out.host = v
		case "--port":
			v, err := value()
			if err != nil {
				return out, err
			}
			port, perr := strconv.Atoi(v)
			if perr != nil || port < 0 || port > 65535 {
				return out, fmt.Errorf("--port is a number, 0 to 65535")
			}
			out.port = port
		case "--today":
			v, err := value()
			if err != nil {
				return out, err
			}
			day, ok := lending.ParseDay(v)
			if !ok {
				return out, fmt.Errorf("--today %q is not YYYY-MM-DD", v)
			}
			out.today = &day
		case "--verbose":
			out.verbose = true
		case "-h", "--help":
			return out, fmt.Errorf("%s", usage)
		default:
			return out, fmt.Errorf("unknown argument %q\n%s", flag, usage)
		}
	}
	return out, nil
}

func main() {
	a, err := parse(os.Args[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "serve: %v\n", err)
		os.Exit(2)
	}
	today := lending.Today()
	if a.today != nil {
		today = *a.today
	}
	server, err := lending.Serve(lending.NewLibrary(), lending.NewClock(today), a.host, a.port, a.verbose)
	if err != nil {
		fmt.Fprintf(os.Stderr, "serve: cannot listen on %s:%d: %v\n", a.host, a.port, err)
		os.Exit(2)
	}
	pinned := ""
	if a.today != nil {
		pinned = ", clock pinned to " + a.today.ISO()
	}
	fmt.Printf("library service on %s%s\n", server.Base, pinned)
	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, os.Interrupt)
	<-interrupt
	_ = server.Stop()
}
