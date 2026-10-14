package lending

// What the HTTP API adds to the rules: status codes, malformed requests, and the clock.

import (
	"strings"
	"testing"
)

type api struct {
	clock  *Clock
	served *Served
}

func newAPI(t *testing.T) *api {
	t.Helper()
	clock := NewClock(DayFromYMD(1970, 1, 1))
	served, err := Serve(NewLibrary(), clock, "127.0.0.1", 0, false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = served.Stop() })
	return &api{clock, served}
}

func (a *api) call(method, path string, body any) (int, map[string]any) {
	status, answer := call(a.served.Base, method, path, body)
	object, _ := answer.(map[string]any)
	return status, object
}

func want(t *testing.T, got, expected any) {
	t.Helper()
	if got != expected {
		t.Errorf("got %v, want %v", got, expected)
	}
}

var title = map[string]any{"isbn": "1", "title": "T", "author": "A"}

func TestACreationIs201AndAReadIs200(t *testing.T) {
	a := newAPI(t)
	created, _ := a.call("POST", "/titles", title)
	want(t, created, 201)
	a.call("POST", "/copies", map[string]any{"copy": "c", "isbn": "1"})
	status, body := a.call("GET", "/copies/c", nil)
	want(t, status, 200)
	want(t, body["status"], "available")
}

func TestUnknownIs404AndARuleIs409(t *testing.T) {
	a := newAPI(t)
	status, body := a.call("GET", "/copies/nope", nil)
	want(t, status, 404)
	want(t, body["error"], "unknown-copy")
	a.call("POST", "/titles", title)
	again, refused := a.call("POST", "/titles", title)
	want(t, again, 409)
	want(t, refused["error"], "duplicate-title")
}

func TestAMalformedRequestIs400AndNamesWhatIsWrong(t *testing.T) {
	a := newAPI(t)
	status, body := callRaw(a.served.Base, "POST", "/titles", strings.NewReader("{not json"))
	want(t, status, 400)
	want(t, body.(map[string]any)["error"], "bad-request")
	short, told := a.call("POST", "/titles", map[string]any{"isbn": "1"})
	want(t, short, 400)
	if !strings.Contains(told["message"].(string), "title, author") {
		t.Error(told["message"])
	}
	extra, said := a.call("POST", "/members", map[string]any{"member": "m", "name": "N", "age": "9"})
	want(t, extra, 400)
	if !strings.Contains(said["message"].(string), "takes no age") {
		t.Error(said["message"])
	}
}

func TestAnUnknownRouteIs404(t *testing.T) {
	a := newAPI(t)
	status, body := a.call("POST", "/loans/overdue", nil)
	want(t, status, 404)
	want(t, body["error"], "no-route")
	other, missing := a.call("GET", "/shelves", nil)
	want(t, other, 404)
	want(t, missing["error"], "no-route")
}

func TestTheDateIsTheServersClockNotTheClients(t *testing.T) {
	a := newAPI(t)
	a.call("POST", "/titles", title)
	a.call("POST", "/copies", map[string]any{"copy": "c", "isbn": "1"})
	a.call("POST", "/members", map[string]any{"member": "m", "name": "N"})
	a.clock.Set(DayFromYMD(2026, 12, 25))
	status, body := a.call("POST", "/loans", map[string]any{"member": "m", "copy": "c"})
	want(t, status, 201)
	want(t, body["lent"], "2026-12-25")
	want(t, body["due"], "2027-01-15")
}

func TestTheRoutesAgreeWithTheVectorForms(t *testing.T) {
	// A guard on the test support itself: every operation a vector names has an HTTP form,
	// with the method that form implies.
	file := loadVectors(t)
	scenarios, _ := file["scenarios"].([]any)
	seen := map[string]bool{}
	for _, item := range scenarios {
		for _, step := range stepsOf(t, file, item.(map[string]any)) {
			op, _ := step["op"].(string)
			args, ok := step["args"].(map[string]any)
			if !ok {
				args = map[string]any{}
			}
			if seen[op] {
				continue
			}
			method, path, body := httpRequest(op, args)
			reads := op == "copy" || op == "loans" || op == "overdue"
			writesABody := op == "add_title" || op == "add_copy" || op == "add_member" || op == "lend"
			if reads != (method == "GET") || writesABody != (body != nil) || !strings.HasPrefix(path, "/") {
				t.Errorf("%s: %s %s", op, method, path)
			}
			seen[op] = true
		}
	}
	want(t, len(seen), 9)
}
