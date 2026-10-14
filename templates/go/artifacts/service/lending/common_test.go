package lending

// Shared test support: the scenario vectors, and the HTTP form of each operation.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"testing"
)

// loadVectors reads openspec/vectors.json, the language-neutral half of the spec. The tests
// run in artifacts/service/lending, three levels below the repository root.
func loadVectors(t *testing.T) map[string]any {
	t.Helper()
	text, err := os.ReadFile(filepath.Join("..", "..", "..", "openspec", "vectors.json"))
	if err != nil {
		t.Fatal(err)
	}
	var file map[string]any
	if err := json.Unmarshal(text, &file); err != nil {
		t.Fatal(err)
	}
	return file
}

// stepsOf is the fixtures a scenario names, then its own steps.
func stepsOf(t *testing.T, file map[string]any, scenario map[string]any) []map[string]any {
	t.Helper()
	fixtures, _ := file["fixtures"].(map[string]any)
	var steps []map[string]any
	add := func(list any) {
		items, _ := list.([]any)
		for _, item := range items {
			if step, ok := item.(map[string]any); ok {
				steps = append(steps, step)
			}
		}
	}
	given, _ := scenario["given"].([]any)
	for _, name := range given {
		fixture, ok := fixtures[fmt.Sprint(name)]
		if !ok {
			t.Fatalf("no fixture %v", name)
		}
		add(fixture)
	}
	add(scenario["steps"])
	return steps
}

// httpRequest is how each operation is asked over HTTP: its method, its path, and the body
// if any.
func httpRequest(op string, args map[string]any) (method, path string, body any) {
	text := func(key string) string {
		found, ok := args[key].(string)
		if !ok {
			panic(op + " needs " + key)
		}
		return url.PathEscape(found)
	}
	switch op {
	case "add_title":
		return "POST", "/titles", args
	case "add_copy":
		return "POST", "/copies", args
	case "add_member":
		return "POST", "/members", args
	case "lend":
		return "POST", "/loans", args
	case "copy":
		return "GET", "/copies/" + text("copy"), nil
	case "renew":
		return "POST", "/loans/" + text("copy") + "/renewal", nil
	case "return":
		return "POST", "/loans/" + text("copy") + "/return", nil
	case "loans":
		return "GET", "/members/" + text("member") + "/loans", nil
	case "overdue":
		return "GET", "/loans/overdue", nil
	}
	panic("no HTTP form for " + op)
}

// call is one request by hand, answering (status, decoded body).
func call(base, method, path string, body any) (int, any) {
	var reader io.Reader
	if body != nil {
		encoded, _ := json.Marshal(body)
		reader = bytes.NewReader(encoded)
	}
	return callRaw(base, method, path, reader)
}

func callRaw(base, method, path string, reader io.Reader) (int, any) {
	request, err := http.NewRequest(method, base+path, reader)
	if err != nil {
		panic(err)
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		panic(err)
	}
	defer response.Body.Close()
	text, _ := io.ReadAll(response.Body)
	var parsed any
	if err := json.Unmarshal(text, &parsed); err != nil {
		panic(fmt.Sprintf("%s %s answered %q", method, path, text))
	}
	return response.StatusCode, parsed
}
