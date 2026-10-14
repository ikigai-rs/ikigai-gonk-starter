// The library's rules: catalog, members, loans, renewals and overdue loans.
//
// Time is an INPUT. Every operation that depends on the date takes today as an argument, and
// nothing in this file reads a clock, so a test, a scenario vector and a book chapter all get
// the same answer on any day they run. Clock is the one thing that holds a date, and the HTTP
// layer is handed the clock it asks.
//
// Every answer is a JSON object (an Answer), the same shape the HTTP API serves and the
// scenario vectors (openspec/vectors.json) expect.
package lending

import (
	"fmt"
	"sort"
	"strings"
	"sync"
)

const (
	LoanDays       = 21
	MaxLoans       = 3
	MaxRenewals    = 2
	FeeCentsPerDay = 25
)

// Refused is an operation the rules refuse. Code is the stable name the specs and vectors use.
type Refused struct {
	Code    string
	Message string
}

func (r *Refused) Error() string { return r.Code + ": " + r.Message }

func refuse(code, format string, args ...any) *Refused {
	return &Refused{Code: code, Message: fmt.Sprintf(format, args...)}
}

// Answer is what every operation answers: a JSON object.
type Answer = map[string]any

type Title struct{ ISBN, Title, Author string }
type Copy struct{ Copy, ISBN string }
type Member struct{ Member, Name string }

type Loan struct {
	Copy, Member string
	Lent, Due    Day
	Renewals     int
}

// DaysLate: a loan is overdue from the day AFTER its due date.
func DaysLate(loan *Loan, today Day) int {
	if late := today.DaysSince(loan.Due); late > 0 {
		return late
	}
	return 0
}

func loanAnswer(loan *Loan) Answer {
	return Answer{
		"copy":     loan.Copy,
		"member":   loan.Member,
		"lent":     loan.Lent.ISO(),
		"due":      loan.Due.ISO(),
		"renewals": loan.Renewals,
	}
}

// Library is one library's state, in memory. Apply serializes its callers; the methods
// themselves do not.
type Library struct {
	mu      sync.Mutex
	titles  map[string]Title
	copies  map[string]Copy
	members map[string]Member
	// loans is by copy: a copy is out to one member at a time.
	loans map[string]*Loan
}

func NewLibrary() *Library {
	return &Library{
		titles:  map[string]Title{},
		copies:  map[string]Copy{},
		members: map[string]Member{},
		loans:   map[string]*Loan{},
	}
}

// ------------------------------------------------------------- catalog and members

func (l *Library) AddTitle(isbn, title, author string) (Answer, error) {
	if _, ok := l.titles[isbn]; ok {
		return nil, refuse("duplicate-title", "ISBN %s is already cataloged", isbn)
	}
	l.titles[isbn] = Title{isbn, title, author}
	return Answer{"isbn": isbn, "title": title, "author": author}, nil
}

func (l *Library) AddCopy(copy, isbn string) (Answer, error) {
	if _, ok := l.titles[isbn]; !ok {
		return nil, refuse("unknown-title", "no title with ISBN %s is cataloged", isbn)
	}
	if _, ok := l.copies[copy]; ok {
		return nil, refuse("duplicate-copy", "copy %s already exists", copy)
	}
	l.copies[copy] = Copy{copy, isbn}
	return l.CopyStatus(copy)
}

func (l *Library) AddMember(member, name string) (Answer, error) {
	if _, ok := l.members[member]; ok {
		return nil, refuse("duplicate-member", "member %s is already registered", member)
	}
	l.members[member] = Member{member, name}
	return Answer{"member": member, "name": name}, nil
}

func (l *Library) CopyStatus(copy string) (Answer, error) {
	found, err := l.copyOf(copy)
	if err != nil {
		return nil, err
	}
	status := "available"
	var loan any
	if out, ok := l.loans[copy]; ok {
		status = "on-loan"
		loan = loanAnswer(out)
	}
	return Answer{"copy": copy, "isbn": found.ISBN, "status": status, "loan": loan}, nil
}

// ---------------------------------------------------------------------------- loans

func (l *Library) Lend(member, copy string, today Day) (Answer, error) {
	if _, err := l.memberOf(member); err != nil {
		return nil, err
	}
	if _, err := l.copyOf(copy); err != nil {
		return nil, err
	}
	if out, ok := l.loans[copy]; ok {
		return nil, refuse("copy-on-loan", "copy %s is out until %s", copy, out.Due.ISO())
	}
	theirs := 0
	for _, loan := range l.loans {
		if loan.Member != member {
			continue
		}
		theirs++
		if DaysLate(loan, today) > 0 {
			return nil, refuse("member-has-overdue", "member %s has an overdue loan", member)
		}
	}
	if theirs >= MaxLoans {
		return nil, refuse("loan-limit", "member %s already has %d loans", member, MaxLoans)
	}
	loan := &Loan{Copy: copy, Member: member, Lent: today, Due: today.PlusDays(LoanDays)}
	l.loans[copy] = loan
	return loanAnswer(loan), nil
}

func (l *Library) Renew(copy string, today Day) (Answer, error) {
	loan, err := l.loanOf(copy)
	if err != nil {
		return nil, err
	}
	if DaysLate(loan, today) > 0 {
		return nil, refuse("loan-overdue", "copy %s was due %s; return it instead", copy, loan.Due.ISO())
	}
	if loan.Renewals >= MaxRenewals {
		return nil, refuse("renewal-limit", "copy %s has been renewed %d times", copy, MaxRenewals)
	}
	loan.Due = loan.Due.PlusDays(LoanDays)
	loan.Renewals++
	return loanAnswer(loan), nil
}

func (l *Library) GiveBack(copy string, today Day) (Answer, error) {
	loan, err := l.loanOf(copy)
	if err != nil {
		return nil, err
	}
	late := DaysLate(loan, today)
	delete(l.loans, copy)
	return Answer{
		"copy":      copy,
		"member":    loan.Member,
		"returned":  today.ISO(),
		"days_late": late,
		"fee_cents": late * FeeCentsPerDay,
	}, nil
}

func (l *Library) LoansOf(member string, today Day) (Answer, error) {
	if _, err := l.memberOf(member); err != nil {
		return nil, err
	}
	var theirs []*Loan
	for _, loan := range l.loans {
		if loan.Member == member {
			theirs = append(theirs, loan)
		}
	}
	sort.Slice(theirs, func(i, j int) bool {
		if theirs[i].Due != theirs[j].Due {
			return theirs[i].Due < theirs[j].Due
		}
		return theirs[i].Copy < theirs[j].Copy
	})
	rows := []any{}
	for _, loan := range theirs {
		title := l.titles[l.copies[loan.Copy].ISBN]
		late := DaysLate(loan, today)
		rows = append(rows, Answer{
			"copy":      loan.Copy,
			"isbn":      title.ISBN,
			"title":     title.Title,
			"due":       loan.Due.ISO(),
			"renewals":  loan.Renewals,
			"overdue":   late > 0,
			"days_late": late,
		})
	}
	return Answer{"member": member, "loans": rows}, nil
}

func (l *Library) Overdue(today Day) (Answer, error) {
	var late []*Loan
	for _, loan := range l.loans {
		if DaysLate(loan, today) > 0 {
			late = append(late, loan)
		}
	}
	sort.Slice(late, func(i, j int) bool {
		a, b := DaysLate(late[i], today), DaysLate(late[j], today)
		if a != b {
			return a > b
		}
		return late[i].Copy < late[j].Copy
	})
	rows := []any{}
	for _, loan := range late {
		rows = append(rows, Answer{
			"copy":      loan.Copy,
			"member":    loan.Member,
			"due":       loan.Due.ISO(),
			"days_late": DaysLate(loan, today),
		})
	}
	return Answer{"overdue": rows}, nil
}

// ------------------------------------------------------------------------- lookups

func (l *Library) memberOf(member string) (Member, error) {
	found, ok := l.members[member]
	if !ok {
		return found, refuse("unknown-member", "no member %s", member)
	}
	return found, nil
}

func (l *Library) copyOf(copy string) (Copy, error) {
	found, ok := l.copies[copy]
	if !ok {
		return found, refuse("unknown-copy", "no copy %s", copy)
	}
	return found, nil
}

func (l *Library) loanOf(copy string) (*Loan, error) {
	if _, err := l.copyOf(copy); err != nil {
		return nil, err
	}
	found, ok := l.loans[copy]
	if !ok {
		return nil, refuse("not-on-loan", "copy %s is not on loan", copy)
	}
	return found, nil
}

// take reads the named arguments of one operation: what is left over when the operation has
// asked for all of them is an argument it does not take, and a name it asks for that is
// absent (or not a non-empty string) is a missing argument. Both are refused, naming every one.
func take(op string, args map[string]any, names ...string) ([]string, error) {
	var extra []string
	for name := range args {
		known := false
		for _, want := range names {
			known = known || want == name
		}
		if !known {
			extra = append(extra, name)
		}
	}
	if len(extra) > 0 {
		sort.Strings(extra)
		return nil, refuse("bad-request", "%s takes no %s", op, strings.Join(extra, ", "))
	}
	var missing []string
	values := make([]string, 0, len(names))
	for _, name := range names {
		value, ok := args[name].(string)
		if !ok || value == "" {
			missing = append(missing, name)
		}
		values = append(values, value)
	}
	if len(missing) > 0 {
		return nil, refuse("bad-request", "%s needs %s (non-empty strings)", op, strings.Join(missing, ", "))
	}
	return values, nil
}

// Apply runs one named operation: the routing table the HTTP layer and a vector-driven test
// both come through. It refuses an unknown operation, a missing argument and an argument the
// operation does not take, rather than guessing. The error is a *Refused.
func Apply(library *Library, op string, args any, today Day) (Answer, error) {
	object, ok := args.(map[string]any)
	if !ok {
		return nil, refuse("bad-request", "the body is a JSON object")
	}
	library.mu.Lock()
	defer library.mu.Unlock()
	var names []string
	switch op {
	case "add_title":
		names = []string{"isbn", "title", "author"}
	case "add_copy":
		names = []string{"copy", "isbn"}
	case "add_member":
		names = []string{"member", "name"}
	case "copy", "renew", "return":
		names = []string{"copy"}
	case "lend":
		names = []string{"member", "copy"}
	case "loans":
		names = []string{"member"}
	case "overdue":
		names = nil
	default:
		return nil, refuse("bad-request", "no operation %q", op)
	}
	v, err := take(op, object, names...)
	if err != nil {
		return nil, err
	}
	switch op {
	case "add_title":
		return library.AddTitle(v[0], v[1], v[2])
	case "add_copy":
		return library.AddCopy(v[0], v[1])
	case "add_member":
		return library.AddMember(v[0], v[1])
	case "copy":
		return library.CopyStatus(v[0])
	case "lend":
		return library.Lend(v[0], v[1], today)
	case "renew":
		return library.Renew(v[0], today)
	case "return":
		return library.GiveBack(v[0], today)
	case "loans":
		return library.LoansOf(v[0], today)
	default:
		return library.Overdue(today)
	}
}
