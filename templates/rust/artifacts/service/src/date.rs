//! Dates, as a plain count of days: the 21 days of a loan, and how late a return is, are
//! integer arithmetic, and a date never depends on a time zone or on the hour it is asked.
//!
//! Time is an INPUT here too. Nothing in the rules reads a clock; `Clock` is the one thing
//! that holds a date, and it is handed to the server, so a test pins it or moves it and gets
//! the same due dates on any day it runs.

use std::sync::atomic::{AtomicI64, Ordering};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Date {
    days: i64,
}

impl Date {
    /// A date from its distance in days from 1970-01-01.
    pub fn from_days(days: i64) -> Date {
        Date { days }
    }

    pub fn days(self) -> i64 {
        self.days
    }

    pub fn from_ymd(year: i64, month: u32, day: u32) -> Date {
        Date {
            days: days_from_civil(year, month as i64, day as i64),
        }
    }

    /// `YYYY-MM-DD`, and nothing else: a date that does not exist is no date.
    pub fn parse(text: &str) -> Option<Date> {
        let parts: Vec<&str> = text.split('-').collect();
        if parts.len() != 3 || parts[0].len() != 4 || parts[1].len() != 2 || parts[2].len() != 2 {
            return None;
        }
        let year: i64 = parts[0].parse().ok()?;
        let month: u32 = parts[1].parse().ok()?;
        let day: u32 = parts[2].parse().ok()?;
        if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
            return None;
        }
        let date = Date::from_ymd(year, month, day);
        if date.to_iso() != text {
            return None; // the 30th of February, say
        }
        Some(date)
    }

    pub fn to_iso(self) -> String {
        let (year, month, day) = civil_from_days(self.days);
        format!("{year:04}-{month:02}-{day:02}")
    }

    pub fn plus_days(self, days: i64) -> Date {
        Date {
            days: self.days + days,
        }
    }

    /// How many days after `other` this date is; negative before it.
    pub fn days_since(self, other: Date) -> i64 {
        self.days - other.days
    }

    /// Today, by this machine's clock, counted in UTC days.
    pub fn today() -> Date {
        let seconds = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|since| since.as_secs() as i64)
            .unwrap_or(0);
        Date::from_days(seconds.div_euclid(86_400))
    }
}

/// The one clock the server asks: fixed to a date, or moved to another, by whoever holds it.
#[derive(Debug)]
pub struct Clock {
    days: AtomicI64,
}

impl Clock {
    pub fn fixed(today: Date) -> Clock {
        Clock {
            days: AtomicI64::new(today.days()),
        }
    }

    pub fn set(&self, today: Date) {
        self.days.store(today.days(), Ordering::SeqCst);
    }

    pub fn today(&self) -> Date {
        Date::from_days(self.days.load(Ordering::SeqCst))
    }
}

// The calendar arithmetic below is Howard Hinnant's `days_from_civil` and `civil_from_days`,
// the standard way to move between a year, month and day and a day count without a library.

fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = if year >= 0 { year } else { year - 399 }.div_euclid(400);
    let year_of_era = year - era * 400; // [0, 399]
    let day_of_year = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    era * 146_097 + day_of_era - 719_468
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let days = days + 719_468;
    let era = if days >= 0 { days } else { days - 146_096 }.div_euclid(146_097);
    let day_of_era = days - era * 146_097; // [0, 146096]
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153; // [0, 11]
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = if month_prime < 10 {
        month_prime + 3
    } else {
        month_prime - 9
    };
    (if month <= 2 { year + 1 } else { year }, month, day)
}
