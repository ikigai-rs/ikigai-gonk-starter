/**
 * Dates, as a plain count of days: the 21 days of a loan, and how late a return is, are
 * integer arithmetic, and a date never depends on a time zone or on the hour it is asked.
 *
 * Time is an INPUT here too. Nothing in the rules reads a clock; `Clock` is the one thing
 * that holds a date, and it is handed to the server, so a test pins it or moves it and gets
 * the same due dates on any day it runs.
 *
 * The class is `Day`, not `Date`: `Date` is the built-in moment in time, and this is a
 * calendar date — the count of days since 1970-01-01, which is all a due date needs.
 */

export class Day {
  /** This date's distance in days from 1970-01-01. */
  readonly days: number;

  private constructor(days: number) {
    this.days = days;
  }

  /** A date from its distance in days from 1970-01-01. */
  static fromDays(days: number): Day {
    return new Day(days);
  }

  static fromYmd(year: number, month: number, day: number): Day {
    return new Day(daysFromCivil(year, month, day));
  }

  /** `YYYY-MM-DD`, and nothing else: a date that does not exist is no date. */
  static parse(text: string): Day | null {
    const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    if (found === null) {
      return null;
    }
    const [, year, month, day] = found;
    const date = Day.fromYmd(Number(year), Number(month), Number(day));
    return date.iso() === text ? date : null; // the 30th of February, say
  }

  iso(): string {
    const [year, month, day] = civilFromDays(this.days);
    return `${pad(year, 4)}-${pad(month, 2)}-${pad(day, 2)}`;
  }

  plusDays(days: number): Day {
    return new Day(this.days + days);
  }

  /** How many days after `other` this date is; negative before it. */
  daysSince(other: Day): number {
    return this.days - other.days;
  }

  /** Today, by this machine's clock, counted in UTC days. */
  static today(): Day {
    return Day.fromDays(Math.floor(Date.now() / 86_400_000));
  }
}

/** The one clock the server asks: fixed to a date, or moved to another, by whoever holds it. */
export class Clock {
  private days: number;

  constructor(today: Day) {
    this.days = today.days;
  }

  set(today: Day): void {
    this.days = today.days;
  }

  today(): Day {
    return Day.fromDays(this.days);
  }
}

function pad(value: number, width: number): string {
  return String(value).padStart(width, "0");
}

// The calendar arithmetic below is Howard Hinnant's `days_from_civil` and `civil_from_days`,
// the standard way to move between a year, month and day and a day count without a library.

function daysFromCivil(year: number, month: number, day: number): number {
  const y = month <= 2 ? year - 1 : year;
  const era = Math.floor(y / 400);
  const yearOfEra = y - era * 400; // [0, 399]
  const dayOfYear = Math.floor((153 * (month > 2 ? month - 3 : month + 9) + 2) / 5) + day - 1;
  const dayOfEra = yearOfEra * 365 + Math.floor(yearOfEra / 4) - Math.floor(yearOfEra / 100) +
    dayOfYear;
  return era * 146_097 + dayOfEra - 719_468;
}

function civilFromDays(days: number): [number, number, number] {
  const shifted = days + 719_468;
  const era = Math.floor(shifted / 146_097);
  const dayOfEra = shifted - era * 146_097; // [0, 146096]
  const yearOfEra = Math.floor(
    (dayOfEra - Math.floor(dayOfEra / 1460) + Math.floor(dayOfEra / 36_524) -
      Math.floor(dayOfEra / 146_096)) / 365,
  );
  const year = yearOfEra + era * 400;
  const dayOfYear = dayOfEra -
    (365 * yearOfEra + Math.floor(yearOfEra / 4) - Math.floor(yearOfEra / 100));
  const monthPrime = Math.floor((5 * dayOfYear + 2) / 153); // [0, 11]
  const day = dayOfYear - Math.floor((153 * monthPrime + 2) / 5) + 1;
  const month = monthPrime < 10 ? monthPrime + 3 : monthPrime - 9;
  return [month <= 2 ? year + 1 : year, month, day];
}
