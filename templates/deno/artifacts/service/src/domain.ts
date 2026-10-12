/**
 * The library's rules: catalog, members, loans, renewals and overdue loans.
 *
 * Time is an INPUT. Every operation that depends on the date takes `today` as an argument,
 * and nothing in this module reads a clock, so a test, a scenario vector and a book chapter
 * all get the same answer on any day they run. `date.Clock` is the one thing that holds a
 * date, and the HTTP layer is handed the clock it asks.
 *
 * Every answer is a `Json` object, the same shape the HTTP API serves and the scenario
 * vectors (`openspec/vectors.json`) expect.
 */

import type { Json } from "./json.ts";
import { isObject } from "./json.ts";
import type { Day } from "./date.ts";

export const LOAN_DAYS = 21;
export const MAX_LOANS = 3;
export const MAX_RENEWALS = 2;
export const FEE_CENTS_PER_DAY = 25;

/** An operation the rules refuse. `code` is the stable name the specs and vectors use. */
export class Refused extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "Refused";
    this.code = code;
  }
}

/** What every operation answers: its result, or the refusal that says why not. */
export type Answer = Json;

export interface Title {
  isbn: string;
  title: string;
  author: string;
}

export interface Copy {
  copy: string;
  isbn: string;
}

export interface Member {
  member: string;
  name: string;
}

export interface Loan {
  copy: string;
  member: string;
  lent: Day;
  due: Day;
  renewals: number;
}

/** A loan is overdue from the day AFTER its due date. */
export function daysLate(loan: Loan, today: Day): number {
  return Math.max(0, today.daysSince(loan.due));
}

function loanAnswer(loan: Loan): Json {
  return {
    copy: loan.copy,
    member: loan.member,
    lent: loan.lent.iso(),
    due: loan.due.iso(),
    renewals: loan.renewals,
  };
}

/** One library's state, in memory. */
export class Library {
  readonly titles = new Map<string, Title>();
  readonly copies = new Map<string, Copy>();
  readonly members = new Map<string, Member>();
  /** by copy: a copy is out to one member at a time */
  readonly loans = new Map<string, Loan>();

  // ------------------------------------------------------------- catalog and members

  addTitle(isbn: string, title: string, author: string): Answer {
    if (this.titles.has(isbn)) {
      throw new Refused("duplicate-title", `ISBN ${isbn} is already cataloged`);
    }
    this.titles.set(isbn, { isbn, title, author });
    return { isbn, title, author };
  }

  addCopy(copy: string, isbn: string): Answer {
    if (!this.titles.has(isbn)) {
      throw new Refused("unknown-title", `no title with ISBN ${isbn} is cataloged`);
    }
    if (this.copies.has(copy)) {
      throw new Refused("duplicate-copy", `copy ${copy} already exists`);
    }
    this.copies.set(copy, { copy, isbn });
    return this.copyStatus(copy);
  }

  addMember(member: string, name: string): Answer {
    if (this.members.has(member)) {
      throw new Refused("duplicate-member", `member ${member} is already registered`);
    }
    this.members.set(member, { member, name });
    return { member, name };
  }

  copyStatus(copy: string): Answer {
    const found = this.copyOf(copy);
    const loan = this.loans.get(copy);
    return {
      copy,
      isbn: found.isbn,
      status: loan === undefined ? "available" : "on-loan",
      loan: loan === undefined ? null : loanAnswer(loan),
    };
  }

  // ---------------------------------------------------------------------------- loans

  lend(member: string, copy: string, today: Day): Answer {
    this.memberOf(member);
    this.copyOf(copy);
    const out = this.loans.get(copy);
    if (out !== undefined) {
      throw new Refused("copy-on-loan", `copy ${copy} is out until ${out.due.iso()}`);
    }
    const theirs = [...this.loans.values()].filter((loan) => loan.member === member);
    if (theirs.some((loan) => daysLate(loan, today) > 0)) {
      throw new Refused("member-has-overdue", `member ${member} has an overdue loan`);
    }
    if (theirs.length >= MAX_LOANS) {
      throw new Refused("loan-limit", `member ${member} already has ${MAX_LOANS} loans`);
    }
    const loan: Loan = {
      copy,
      member,
      lent: today,
      due: today.plusDays(LOAN_DAYS),
      renewals: 0,
    };
    this.loans.set(copy, loan);
    return loanAnswer(loan);
  }

  renew(copy: string, today: Day): Answer {
    const loan = this.loanOf(copy);
    if (daysLate(loan, today) > 0) {
      throw new Refused(
        "loan-overdue",
        `copy ${copy} was due ${loan.due.iso()}; return it instead`,
      );
    }
    if (loan.renewals >= MAX_RENEWALS) {
      throw new Refused("renewal-limit", `copy ${copy} has been renewed ${MAX_RENEWALS} times`);
    }
    loan.due = loan.due.plusDays(LOAN_DAYS);
    loan.renewals += 1;
    return loanAnswer(loan);
  }

  giveBack(copy: string, today: Day): Answer {
    const loan = this.loanOf(copy);
    const late = daysLate(loan, today);
    const answer: Json = {
      copy,
      member: loan.member,
      returned: today.iso(),
      days_late: late,
      fee_cents: late * FEE_CENTS_PER_DAY,
    };
    this.loans.delete(copy);
    return answer;
  }

  loansOf(member: string, today: Day): Answer {
    this.memberOf(member);
    const theirs = [...this.loans.values()]
      .filter((loan) => loan.member === member)
      .sort((a, b) => a.due.days - b.due.days || compare(a.copy, b.copy));
    const rows: Json[] = theirs.map((loan) => {
      const title = this.titles.get(this.copies.get(loan.copy)!.isbn)!;
      const late = daysLate(loan, today);
      return {
        copy: loan.copy,
        isbn: title.isbn,
        title: title.title,
        due: loan.due.iso(),
        renewals: loan.renewals,
        overdue: late > 0,
        days_late: late,
      };
    });
    return { member, loans: rows };
  }

  overdue(today: Day): Answer {
    const late = [...this.loans.values()]
      .filter((loan) => daysLate(loan, today) > 0)
      .sort((a, b) => daysLate(b, today) - daysLate(a, today) || compare(a.copy, b.copy));
    const rows: Json[] = late.map((loan) => ({
      copy: loan.copy,
      member: loan.member,
      due: loan.due.iso(),
      days_late: daysLate(loan, today),
    }));
    return { overdue: rows };
  }

  // ------------------------------------------------------------------------- lookups

  private memberOf(member: string): Member {
    const found = this.members.get(member);
    if (found === undefined) {
      throw new Refused("unknown-member", `no member ${member}`);
    }
    return found;
  }

  private copyOf(copy: string): Copy {
    const found = this.copies.get(copy);
    if (found === undefined) {
      throw new Refused("unknown-copy", `no copy ${copy}`);
    }
    return found;
  }

  private loanOf(copy: string): Loan {
    this.copyOf(copy);
    const found = this.loans.get(copy);
    if (found === undefined) {
      throw new Refused("not-on-loan", `copy ${copy} is not on loan`);
    }
    return found;
  }
}

/** A string comparison that does not depend on the machine's locale. */
function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/**
 * The named arguments of one operation: what is left over when the operation has asked for
 * all of them is an argument it does not take, and a name it asks for that is absent (or
 * not a non-empty string) is a missing argument. Both are refused, naming every one.
 */
function take(op: string, args: { [key: string]: Json }, names: string[]): string[] {
  const extra = Object.keys(args).filter((name) => !names.includes(name));
  if (extra.length > 0) {
    throw new Refused("bad-request", `${op} takes no ${extra.join(", ")}`);
  }
  const missing = names.filter((name) => {
    const value = args[name];
    return typeof value !== "string" || value === "";
  });
  if (missing.length > 0) {
    throw new Refused("bad-request", `${op} needs ${missing.join(", ")} (non-empty strings)`);
  }
  return names.map((name) => args[name] as string);
}

/**
 * Run one named operation: the routing table the HTTP layer and a vector-driven test both
 * come through. Refuses an unknown operation, a missing argument and an argument the
 * operation does not take, rather than guessing.
 */
export function apply(library: Library, op: string, args: unknown, today: Day): Answer {
  if (!isObject(args)) {
    throw new Refused("bad-request", "the body is a JSON object");
  }
  switch (op) {
    case "add_title": {
      const values = take(op, args, ["isbn", "title", "author"]);
      return library.addTitle(values[0], values[1], values[2]);
    }
    case "add_copy": {
      const values = take(op, args, ["copy", "isbn"]);
      return library.addCopy(values[0], values[1]);
    }
    case "add_member": {
      const values = take(op, args, ["member", "name"]);
      return library.addMember(values[0], values[1]);
    }
    case "copy": {
      const values = take(op, args, ["copy"]);
      return library.copyStatus(values[0]);
    }
    case "lend": {
      const values = take(op, args, ["member", "copy"]);
      return library.lend(values[0], values[1], today);
    }
    case "renew": {
      const values = take(op, args, ["copy"]);
      return library.renew(values[0], today);
    }
    case "return": {
      const values = take(op, args, ["copy"]);
      return library.giveBack(values[0], today);
    }
    case "loans": {
      const values = take(op, args, ["member"]);
      return library.loansOf(values[0], today);
    }
    case "overdue": {
      take(op, args, []);
      return library.overdue(today);
    }
    default:
      throw new Refused("bad-request", `no operation ${JSON.stringify(op)}`);
  }
}
