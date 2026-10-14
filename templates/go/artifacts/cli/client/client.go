// Package client is the library's command line: a client of the service's HTTP JSON API.
//
//	library [--service URL] <command> …
//
//	add-title ISBN TITLE AUTHOR    catalog a title
//	add-copy COPY ISBN             add a copy of it
//	add-member MEMBER NAME         register a member
//	copy COPY                      a copy, and its loan if it is out
//	lend MEMBER COPY               lend a copy
//	renew COPY                     renew its loan
//	return COPY                    return it
//	loans MEMBER                   a member's loans
//	overdue                        every overdue loan
//
// The service is http://127.0.0.1:8080 unless --service names another. The client never sends
// a date: the service's clock dates every request. Exit status: 0 done, 1 refused by the
// service (its code and message on stderr), 2 the service could not be reached or the command
// line is wrong.
//
// The HTTP client is net/http, standard library, no dependencies.
package client

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const DefaultService = "http://127.0.0.1:8080"

// ErrUnreachable: the service could not be reached.
var ErrUnreachable = errors.New("unreachable")

type Client struct {
	base string
	http *http.Client
}

func New(base string) *Client {
	return &Client{base: strings.TrimRight(base, "/"), http: &http.Client{Timeout: 10 * time.Second}}
}

// Call makes one request, answered as (status, body).
func (c *Client) Call(method, path string, body any) (int, any, error) {
	var reader io.Reader
	if body != nil {
		encoded, _ := json.Marshal(body)
		reader = bytes.NewReader(encoded)
	}
	request, err := http.NewRequest(method, c.base+path, reader)
	if err != nil {
		return 0, nil, fmt.Errorf("%w: no answer from %s: %v", ErrUnreachable, c.base, err)
	}
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := c.http.Do(request)
	if err != nil {
		return 0, nil, fmt.Errorf("%w: no answer from %s: %v", ErrUnreachable, c.base, err)
	}
	defer response.Body.Close()
	text, _ := io.ReadAll(response.Body)
	var parsed any
	if len(text) > 0 && json.Unmarshal(text, &parsed) != nil {
		parsed = map[string]any{"error": fmt.Sprintf("http-%d", response.StatusCode), "message": response.Status}
	}
	return response.StatusCode, parsed, nil
}

// seg is one path segment, so an id with a `/` or a space stays one segment.
func seg(value string) string { return url.PathEscape(value) }

func dollars(cents int) string { return fmt.Sprintf("$%d.%02d", cents/100, cents%100) }

func textOf(value any, key string) string {
	if object, ok := value.(map[string]any); ok {
		if text, ok := object[key].(string); ok {
			return text
		}
	}
	return ""
}

func numberOf(value any, key string) int {
	if object, ok := value.(map[string]any); ok {
		if number, ok := object[key].(float64); ok {
			return int(number)
		}
	}
	return 0
}

func showLoan(loan any) string {
	renewed := ""
	if n := numberOf(loan, "renewals"); n > 0 {
		renewed = fmt.Sprintf(", renewed %dx", n)
	}
	return fmt.Sprintf("%s to %s, due %s%s", textOf(loan, "copy"), textOf(loan, "member"), textOf(loan, "due"), renewed)
}

var commands = []struct {
	name string
	args int
}{
	{"add-title", 3}, {"add-copy", 2}, {"add-member", 2}, {"copy", 1}, {"lend", 2},
	{"renew", 1}, {"return", 1}, {"loans", 1}, {"overdue", 0},
}

// Usage is the command line's help.
var Usage = func() string {
	lines := []string{"usage: library [--service URL] <command> …"}
	for _, command := range commands {
		lines = append(lines, strings.TrimRight("  "+command.name+" "+strings.Repeat("ARG ", command.args), " "))
	}
	return strings.Join(lines, "\n")
}()

// Command is a command line, already parsed: the command, its arguments, and where the
// service is.
type Command struct {
	Service string
	Name    string
	Args    []string
}

func Parse(argv []string) (Command, error) {
	service := DefaultService
	at := 0
loop:
	for at < len(argv) {
		switch argv[at] {
		case "--service":
			if at+1 >= len(argv) {
				return Command{}, errors.New("--service needs a URL")
			}
			service = argv[at+1]
			at += 2
		case "-h", "--help":
			return Command{}, errors.New(Usage)
		default:
			break loop
		}
	}
	if at >= len(argv) {
		return Command{}, errors.New(Usage)
	}
	name, args := argv[at], argv[at+1:]
	for _, command := range commands {
		if command.name == name {
			if len(args) != command.args {
				return Command{}, fmt.Errorf("%s takes %d argument(s)\n%s", name, command.args, Usage)
			}
			return Command{Service: service, Name: name, Args: args}, nil
		}
	}
	return Command{}, fmt.Errorf("unknown command %q\n%s", name, Usage)
}

func request(command Command) (method, path string, body any) {
	a := command.Args
	switch command.Name {
	case "add-title":
		return "POST", "/titles", map[string]any{"isbn": a[0], "title": a[1], "author": a[2]}
	case "add-copy":
		return "POST", "/copies", map[string]any{"copy": a[0], "isbn": a[1]}
	case "add-member":
		return "POST", "/members", map[string]any{"member": a[0], "name": a[1]}
	case "copy":
		return "GET", "/copies/" + seg(a[0]), nil
	case "lend":
		return "POST", "/loans", map[string]any{"member": a[0], "copy": a[1]}
	case "renew":
		return "POST", "/loans/" + seg(a[0]) + "/renewal", nil
	case "return":
		return "POST", "/loans/" + seg(a[0]) + "/return", nil
	case "loans":
		return "GET", "/members/" + seg(a[0]) + "/loans", nil
	default:
		return "GET", "/loans/overdue", nil
	}
}

func rowsOf(body any, key string) []map[string]any {
	var rows []map[string]any
	if object, ok := body.(map[string]any); ok {
		if list, ok := object[key].([]any); ok {
			for _, row := range list {
				if object, ok := row.(map[string]any); ok {
					rows = append(rows, object)
				}
			}
		}
	}
	return rows
}

func said(command Command, body any) string {
	switch command.Name {
	case "add-title":
		return fmt.Sprintf("cataloged %s: %s by %s", textOf(body, "isbn"), textOf(body, "title"), textOf(body, "author"))
	case "add-copy":
		return fmt.Sprintf("added copy %s of %s", textOf(body, "copy"), textOf(body, "isbn"))
	case "add-member":
		return fmt.Sprintf("registered %s: %s", textOf(body, "member"), textOf(body, "name"))
	case "copy":
		state := "available"
		if object, ok := body.(map[string]any); ok {
			if loan, ok := object["loan"].(map[string]any); ok {
				state = "on loan, " + showLoan(loan)
			}
		}
		return fmt.Sprintf("%s (%s): %s", textOf(body, "copy"), textOf(body, "isbn"), state)
	case "lend":
		return "lent " + showLoan(body)
	case "renew":
		return "renewed " + showLoan(body)
	case "return":
		owes := ""
		if late := numberOf(body, "days_late"); late > 0 {
			owes = fmt.Sprintf(", %d day(s) late: owes %s", late, dollars(numberOf(body, "fee_cents")))
		}
		return fmt.Sprintf("returned %s from %s%s", textOf(body, "copy"), textOf(body, "member"), owes)
	case "loans":
		var lines []string
		for _, row := range rowsOf(body, "loans") {
			overdue := ""
			if row["overdue"] == true {
				overdue = fmt.Sprintf("  OVERDUE %d day(s)", numberOf(row, "days_late"))
			}
			lines = append(lines, fmt.Sprintf("%-8s due %s%s  %s", textOf(row, "copy"), textOf(row, "due"), overdue, textOf(row, "title")))
		}
		if len(lines) == 0 {
			return command.Args[0] + " has no loans"
		}
		return strings.Join(lines, "\n")
	default:
		var lines []string
		for _, row := range rowsOf(body, "overdue") {
			lines = append(lines, fmt.Sprintf("%-8s %-10s due %s  %d day(s) late",
				textOf(row, "copy"), textOf(row, "member"), textOf(row, "due"), numberOf(row, "days_late")))
		}
		if len(lines) == 0 {
			return "nothing is overdue"
		}
		return strings.Join(lines, "\n")
	}
}

// Run runs one command, writing what it did to out and why it failed to errOut. The exit
// status is 0 done, 1 refused by the service, 2 the service did not answer.
func Run(command Command, client *Client, out, errOut io.Writer) int {
	method, path, body := request(command)
	status, answer, err := client.Call(method, path, body)
	if err != nil {
		fmt.Fprintf(errOut, "library: %s\n", strings.TrimPrefix(err.Error(), ErrUnreachable.Error()+": "))
		return 2
	}
	if status >= 400 {
		fmt.Fprintf(errOut, "library: refused (%s): %s\n", textOf(answer, "error"), textOf(answer, "message"))
		return 1
	}
	fmt.Fprintln(out, said(command, answer))
	return 0
}
