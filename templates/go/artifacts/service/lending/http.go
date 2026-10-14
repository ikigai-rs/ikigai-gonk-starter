package lending

// The library's HTTP JSON API: a routing table over Apply, and nothing else.
//
//	POST /titles                 {"isbn", "title", "author"}   catalog a title
//	POST /copies                 {"copy", "isbn"}              add a copy of it
//	POST /members                {"member", "name"}            register a member
//	GET  /copies/{copy}                                        a copy, and its loan if out
//	POST /loans                  {"member", "copy"}            lend a copy
//	POST /loans/{copy}/renewal                                 renew its loan
//	POST /loans/{copy}/return                                  return it
//	GET  /members/{member}/loans                               a member's loans
//	GET  /loans/overdue                                        every overdue loan
//
// A refusal is {"error": <code>, "message": <text>} with 404 for something unknown, 400 for a
// malformed request and 409 for a rule. The DATE of every request is the server's clock,
// never the client's: the clock is an argument of Serve, so a test hands it a fixed or
// settable one and `serve --today` pins it for a demonstration.
//
// The wire is net/http, standard library and no dependencies. The body of an operation whose
// arguments come from it is read here, and a body that is not a JSON object is refused rather
// than guessed at.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"strconv"
	"strings"
)

type route struct {
	method, pattern, op string
	fromBody            bool
}

var routes = []route{
	{"POST", "/titles", "add_title", true},
	{"POST", "/copies", "add_copy", true},
	{"POST", "/members", "add_member", true},
	{"GET", "/copies/{copy}", "copy", false},
	{"POST", "/loans", "lend", true},
	{"GET", "/loans/overdue", "overdue", false},
	{"POST", "/loans/{copy}/renewal", "renew", false},
	{"POST", "/loans/{copy}/return", "return", false},
	{"GET", "/members/{member}/loans", "loans", false},
}

// MaxBody is the largest request body the server reads.
const MaxBody = 64 * 1024

func statusFor(code string) int {
	switch code {
	case "unknown-title", "unknown-copy", "unknown-member", "no-route":
		return 404
	case "bad-request":
		return 400
	default:
		return 409
	}
}

// matchPath is the whole path, segment by segment, so `/copies/{copy}` takes one segment and
// nothing across a `/`. A captured segment is as it came: the other targets' servers do not
// decode either, and the client is the one that percent-encodes an id.
func matchPath(pattern, path string) (map[string]any, bool) {
	want := strings.Split(pattern, "/")
	have := strings.Split(path, "/")
	if len(want) != len(have) {
		return nil, false
	}
	captured := map[string]any{}
	for at, wanted := range want {
		if strings.HasPrefix(wanted, "{") && strings.HasSuffix(wanted, "}") {
			if have[at] == "" {
				return nil, false
			}
			captured[wanted[1:len(wanted)-1]] = have[at]
		} else if wanted != have[at] {
			return nil, false
		}
	}
	return captured, true
}

func refusal(code, message string) Answer { return Answer{"error": code, "message": message} }

// dispatch answers one request: the status, and the JSON body.
func dispatch(r *http.Request, library *Library, clock *Clock) (int, Answer) {
	path := r.URL.EscapedPath()
	for _, rt := range routes {
		if rt.method != r.Method {
			continue
		}
		captured, ok := matchPath(rt.pattern, path)
		if !ok {
			continue
		}
		var args any = captured
		if rt.fromBody {
			text, err := io.ReadAll(io.LimitReader(r.Body, MaxBody+1))
			if err != nil {
				return 400, refusal("bad-request", "the body could not be read: "+err.Error())
			}
			if len(text) > MaxBody {
				return 400, refusal("bad-request", fmt.Sprintf("a body is at most %d bytes", MaxBody))
			}
			args = map[string]any{}
			if len(strings.TrimSpace(string(text))) > 0 {
				var parsed any
				if err := json.Unmarshal(text, &parsed); err != nil {
					return 400, refusal("bad-request", "the body is not JSON: "+err.Error())
				}
				if _, ok := parsed.(map[string]any); !ok {
					return 400, refusal("bad-request", "the body is a JSON object")
				}
				args = parsed
			}
		}
		answer, err := Apply(library, rt.op, args, clock.Today())
		if err != nil {
			var refused *Refused
			if errors.As(err, &refused) {
				return statusFor(refused.Code), refusal(refused.Code, refused.Message)
			}
			return 500, refusal("internal", err.Error())
		}
		switch rt.op {
		case "add_title", "add_copy", "add_member", "lend":
			return 201, answer
		}
		return 200, answer
	}
	return 404, refusal("no-route", fmt.Sprintf("no route %s %s", r.Method, path))
}

// Served is a running server, and the address it answers on.
type Served struct {
	// Base is `http://host:port`.
	Base   string
	server *http.Server
}

// Stop stops the server and lets it go.
func (s *Served) Stop() error {
	return s.server.Shutdown(context.Background())
}

// Serve serves library, dating every request with clock. Port 0 picks a free one, and the
// server is listening (its address known) before this returns.
func Serve(library *Library, clock *Clock, host string, port int, verbose bool) (*Served, error) {
	listener, err := net.Listen("tcp", net.JoinHostPort(host, strconv.Itoa(port)))
	if err != nil {
		return nil, err
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		status, body := 400, refusal("bad-request", fmt.Sprintf("a body is at most %d bytes", MaxBody))
		// A body larger than the limit is refused before it is read.
		if r.ContentLength <= MaxBody {
			status, body = dispatch(r, library, clock)
		}
		if verbose {
			log.Printf("%s %s -> %d", r.Method, r.URL.EscapedPath(), status)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(body)
	})
	server := &http.Server{Handler: handler}
	go func() { _ = server.Serve(listener) }()
	return &Served{Base: "http://" + listener.Addr().String(), server: server}, nil
}
