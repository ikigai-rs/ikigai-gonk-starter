package lending

// The spec's scenarios, run as tests: every entry of openspec/vectors.json, twice.
//
// Once against the rules directly (Apply) and once over HTTP against a real server on a free
// port, whose clock the test moves to each step's date. The vector file is the language-neutral
// half: every language's target runs the same file, so no target can say something different
// from the spec without a test failing.

import (
	"encoding/json"
	"errors"
	"reflect"
	"strconv"
	"testing"
)

// answered is what one step answers: the refusal's code, or the whole answer.
type answered struct {
	refused string
	answer  any
}

type runner interface {
	run(op string, args map[string]any, today Day) answered
	stop()
}

// direct runs a step against the rules, in process.
type direct struct{ library *Library }

func (d direct) run(op string, args map[string]any, today Day) answered {
	answer, err := Apply(d.library, op, args, today)
	var refused *Refused
	if errors.As(err, &refused) {
		return answered{refused: refused.Code}
	}
	if err != nil {
		panic(err)
	}
	return answered{answer: normalize(answer)}
}
func (direct) stop() {}

// overHTTP runs a step against a real server, after setting its clock to the step's date.
type overHTTP struct {
	clock  *Clock
	served *Served
}

func newOverHTTP(t *testing.T) *overHTTP {
	clock := NewClock(DayFromYMD(1970, 1, 1))
	served, err := Serve(NewLibrary(), clock, "127.0.0.1", 0, false)
	if err != nil {
		t.Fatal(err)
	}
	return &overHTTP{clock, served}
}

func (o *overHTTP) run(op string, args map[string]any, today Day) answered {
	o.clock.Set(today)
	method, path, body := httpRequest(op, args)
	status, answer := call(o.served.Base, method, path, body)
	if status >= 400 {
		code, _ := answer.(map[string]any)["error"].(string)
		return answered{refused: code}
	}
	return answered{answer: answer}
}
func (o *overHTTP) stop() { _ = o.served.Stop() }

// normalize is a value as JSON decoding would give it, so an answer built in process (ints)
// compares equal to one read from the vectors or off the wire (float64s).
func normalize(value any) any {
	text, _ := json.Marshal(value)
	var out any
	_ = json.Unmarshal(text, &out)
	return out
}

func checkVectors(t *testing.T, makeRunner func() runner) {
	file := loadVectors(t)
	base, ok := ParseDay(file["today"].(string))
	if !ok {
		t.Fatal("today: not a date")
	}
	scenarios, _ := file["scenarios"].([]any)
	ran := 0
	for _, item := range scenarios {
		scenario := item.(map[string]any)
		id, _ := scenario["id"].(string)
		r := makeRunner()
		today := base
		for n, step := range stepsOf(t, file, scenario) {
			if on, ok := step["on"].(string); ok {
				if today, ok = ParseDay(on); !ok {
					t.Fatalf("%s: a step's date %q is not a date", id, on)
				}
			}
			op, _ := step["op"].(string)
			args, ok := step["args"].(map[string]any)
			if !ok {
				args = map[string]any{}
			}
			where := func() string {
				text, _ := json.Marshal(args)
				return id + " step " + strconv.Itoa(n) + " (" + op + " " + string(text) + " on " + today.ISO() + ")"
			}
			got := r.run(op, args, today)
			if want, ok := step["refused"].(string); ok {
				if got.refused != want {
					t.Errorf("%s\n  refused %q, want %q", where(), got.refused, want)
				}
				continue
			}
			if got.refused != "" {
				t.Errorf("%s: refused (%s)", where(), got.refused)
				continue
			}
			if want, ok := step["expect"]; ok && !reflect.DeepEqual(got.answer, want) {
				g, _ := json.Marshal(got.answer)
				w, _ := json.Marshal(want)
				t.Errorf("%s\n  got:  %s\n  want: %s", where(), g, w)
			}
			ran++
		}
		r.stop()
	}
	if ran == 0 {
		t.Fatal("no scenario ran: the vectors were not read")
	}
}

func TestEveryScenarioAgainstTheRules(t *testing.T) {
	checkVectors(t, func() runner { return direct{NewLibrary()} })
}

func TestEveryScenarioOverHTTP(t *testing.T) {
	checkVectors(t, func() runner { return newOverHTTP(t) })
}
