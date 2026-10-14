// Dates, as a plain count of days: the 21 days of a loan, and how late a return is, are
// integer arithmetic, and a date never depends on a time zone or on the hour it is asked.
//
// Time is an INPUT here too. Nothing in the rules reads a clock; Clock is the one thing that
// holds a date, and it is handed to the server, so a test pins it or moves it and gets the
// same due dates on any day it runs.
package lending

import (
	"sync"
	"time"
)

// Day is a calendar date: its distance in days from 1970-01-01.
type Day int64

// DayFromYMD is the date of a year, month and day.
func DayFromYMD(year int, month time.Month, day int) Day {
	return Day(time.Date(year, month, day, 0, 0, 0, 0, time.UTC).Unix() / 86400)
}

// ParseDay reads `YYYY-MM-DD`, and nothing else: a date that does not exist is no date.
func ParseDay(text string) (Day, bool) {
	t, err := time.Parse("2006-01-02", text)
	if err != nil || t.Format("2006-01-02") != text {
		return 0, false
	}
	return Day(t.Unix() / 86400), true
}

// ISO is the date as `YYYY-MM-DD`.
func (d Day) ISO() string {
	return time.Unix(int64(d)*86400, 0).UTC().Format("2006-01-02")
}

// PlusDays is the date n days later.
func (d Day) PlusDays(n int) Day { return d + Day(n) }

// DaysSince is how many days after other this date is; negative before it.
func (d Day) DaysSince(other Day) int { return int(d - other) }

// Today is today by this machine's clock, counted in UTC days.
func Today() Day { return Day(time.Now().Unix() / 86400) }

// Clock is the one clock the server asks: fixed to a date, or moved to another, by whoever
// holds it.
type Clock struct {
	mu  sync.Mutex
	day Day
}

// NewClock is a clock showing today.
func NewClock(today Day) *Clock { return &Clock{day: today} }

// Set moves the clock to another date.
func (c *Clock) Set(today Day) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.day = today
}

// Today is the date the clock shows.
func (c *Clock) Today() Day {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.day
}
