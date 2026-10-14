package client

// The command line against the real service, on a free port with a clock the test sets.
//
// The CLI speaks the service's JSON, and its tests start the real service in process, on a
// free port, with a clock the test sets, exactly as the other targets' do. The service is the
// sample's other artifact, so it is imported by module path, never from a registry.

import (
	"bytes"
	"net"
	"strings"
	"testing"

	"library-lending/artifacts/service/lending"
)

type cli struct {
	t      *testing.T
	clock  *lending.Clock
	served *lending.Served
}

func newCLI(t *testing.T, today string) *cli {
	t.Helper()
	day, ok := lending.ParseDay(today)
	if !ok {
		t.Fatalf("%s is not a date", today)
	}
	clock := lending.NewClock(day)
	served, err := lending.Serve(lending.NewLibrary(), clock, "127.0.0.1", 0, false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = served.Stop() })
	return &cli{t, clock, served}
}

func (c *cli) set(today string) {
	day, _ := lending.ParseDay(today)
	c.clock.Set(day)
}

// run is the command line, as `library --service <url> …` would be parsed, and its run.
func (c *cli) run(argv ...string) (int, string, string) {
	command, err := Parse(append([]string{"--service", c.served.Base}, argv...))
	if err != nil {
		c.t.Fatal(err)
	}
	var out, errOut bytes.Buffer
	status := Run(command, New(command.Service), &out, &errOut)
	return status, strings.TrimRight(out.String(), "\n"), strings.TrimRight(errOut.String(), "\n")
}

func (c *cli) said(argv ...string) string {
	_, out, _ := c.run(argv...)
	return out
}

func (c *cli) shelf() {
	for _, argv := range [][]string{
		{"add-title", "9780141439518", "Pride and Prejudice", "Jane Austen"},
		{"add-copy", "pp-1", "9780141439518"},
		{"add-copy", "pp-2", "9780141439518"},
		{"add-member", "ada", "Ada Lovelace"},
		{"add-member", "grace", "Grace Hopper"},
	} {
		if status, _, err := c.run(argv...); status != 0 {
			c.t.Fatalf("%s: %s", strings.Join(argv, " "), err)
		}
	}
}

func same(t *testing.T, got, want any) {
	t.Helper()
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestCatalogingAnswersInWords(t *testing.T) {
	c := newCLI(t, "2026-10-01")
	status, out, errOut := c.run("add-title", "9780141439518", "Pride and Prejudice", "Jane Austen")
	same(t, status, 0)
	same(t, out, "cataloged 9780141439518: Pride and Prejudice by Jane Austen")
	same(t, errOut, "")
}

func TestALoanFromLendingToALateReturn(t *testing.T) {
	c := newCLI(t, "2026-10-01")
	c.shelf()
	same(t, c.said("lend", "ada", "pp-1"), "lent pp-1 to ada, due 2026-10-22")
	same(t, c.said("copy", "pp-1"), "pp-1 (9780141439518): on loan, pp-1 to ada, due 2026-10-22")
	same(t, c.said("renew", "pp-1"), "renewed pp-1 to ada, due 2026-11-12, renewed 1x")
	c.set("2026-11-14")
	status, out, _ := c.run("loans", "ada")
	same(t, status, 0)
	same(t, out, "pp-1     due 2026-11-12  OVERDUE 2 day(s)  Pride and Prejudice")
	same(t, c.said("overdue"), "pp-1     ada        due 2026-11-12  2 day(s) late")
	same(t, c.said("return", "pp-1"), "returned pp-1 from ada, 2 day(s) late: owes $0.50")
	same(t, c.said("overdue"), "nothing is overdue")
	same(t, c.said("loans", "ada"), "ada has no loans")
}

func TestARefusalIsExit1WithTheServicesCode(t *testing.T) {
	c := newCLI(t, "2026-10-01")
	c.shelf()
	c.run("lend", "ada", "pp-1")
	status, out, errOut := c.run("lend", "grace", "pp-1")
	same(t, status, 1)
	same(t, out, "")
	if !strings.HasPrefix(errOut, "library: refused (copy-on-loan): copy pp-1 is out") {
		t.Error(errOut)
	}
}

func TestNoServiceIsExit2(t *testing.T) {
	// A port nothing listens on: bound and then released, so nothing can be racing for it.
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	base := "http://" + listener.Addr().String()
	listener.Close()
	command, err := Parse([]string{"--service", base, "overdue"})
	if err != nil {
		t.Fatal(err)
	}
	var out, errOut bytes.Buffer
	same(t, Run(command, New(command.Service), &out, &errOut), 2)
	if !strings.Contains(errOut.String(), "no answer from") {
		t.Error(errOut.String())
	}
}
