/**
 * The library's command line: a client of the service's HTTP JSON API.
 *
 * ```text
 * library [--service URL] <command> …
 *
 * add-title ISBN TITLE AUTHOR    catalog a title
 * add-copy COPY ISBN             add a copy of it
 * add-member MEMBER NAME         register a member
 * copy COPY                      a copy, and its loan if it is out
 * lend MEMBER COPY               lend a copy
 * renew COPY                     renew its loan
 * return COPY                    return it
 * loans MEMBER                   a member's loans
 * overdue                        every overdue loan
 * ```
 *
 * The service is `http://127.0.0.1:8080` unless `--service` names another. The client never
 * sends a date: the service's clock dates every request. Exit status: 0 done, 1 refused by
 * the service (its code and message on stderr), 2 the service could not be reached or the
 * command line is wrong.
 *
 * The HTTP client is Deno's own `fetch`, standard library, no dependencies.
 */

import type { Json } from "../../service/src/json.ts";
import { isObject } from "../../service/src/json.ts";

export const DEFAULT_SERVICE = "http://127.0.0.1:8080";
const TIMEOUT_MS = 10_000;

/** The service could not be reached, or answered something that is not an answer. */
export class Unreachable extends Error {
  constructor(message: string) {
    super(message);
    this.name = "Unreachable";
  }
}

export class Client {
  readonly base: string;

  constructor(base: string) {
    this.base = base.replace(/\/+$/, "");
  }

  /** One request, answered as (status, body). */
  async call(method: string, path: string, body?: Json): Promise<[number, Json]> {
    let response: Response;
    try {
      response = await fetch(this.base + path, {
        method,
        body: body === undefined ? undefined : JSON.stringify(body),
        headers: body === undefined ? undefined : { "content-type": "application/json" },
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (e) {
      throw new Unreachable(`no answer from ${this.base}: ${e}`);
    }
    const text = await response.text();
    let parsed: Json;
    try {
      parsed = text === "" ? null : JSON.parse(text);
    } catch {
      parsed = { error: `http-${response.status}`, message: response.statusText };
    }
    return [response.status, parsed];
  }
}

/** One path segment, so an id with a `/` or a space stays one segment. */
export function seg(value: string): string {
  return encodeURIComponent(value);
}

function dollars(cents: number): string {
  return `$${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
}

function textOf(value: Json, key: string): string {
  if (!isObject(value)) {
    return "";
  }
  const member = value[key];
  return typeof member === "string" ? member : "";
}

function numberOf(value: Json, key: string): number {
  if (!isObject(value)) {
    return 0;
  }
  const member = value[key];
  return typeof member === "number" ? member : 0;
}

function showLoan(loan: Json): string {
  const renewals = numberOf(loan, "renewals");
  const renewed = renewals > 0 ? `, renewed ${renewals}x` : "";
  return `${textOf(loan, "copy")} to ${textOf(loan, "member")}, due ${
    textOf(loan, "due")
  }${renewed}`;
}

const COMMANDS: Array<{ name: string; args: number; about: string }> = [
  { name: "add-title", args: 3, about: "catalog a title" },
  { name: "add-copy", args: 2, about: "add a copy of a cataloged title" },
  { name: "add-member", args: 2, about: "register a member" },
  { name: "copy", args: 1, about: "a copy, and its loan if it is out" },
  { name: "lend", args: 2, about: "lend a copy to a member" },
  { name: "renew", args: 1, about: "renew a loan" },
  { name: "return", args: 1, about: "return a copy" },
  { name: "loans", args: 1, about: "a member's loans" },
  { name: "overdue", args: 0, about: "every overdue loan" },
];

export const USAGE = [
  "usage: library [--service URL] <command> …",
  ...COMMANDS.map((command) =>
    `  ${command.name} ${"ARG ".repeat(command.args).trimEnd()}`.trimEnd()
  ),
].join("\n");

/** A command line, already parsed: the command, its arguments, and where the service is. */
export interface Command {
  service: string;
  name: string;
  args: string[];
}

export function parse(argv: string[]): Command {
  let service = DEFAULT_SERVICE;
  let at = 0;
  while (at < argv.length) {
    const word = argv[at];
    if (word === "--service") {
      if (at + 1 >= argv.length) {
        throw new Error("--service needs a URL");
      }
      service = argv[at + 1];
      at += 2;
    } else if (word === "-h" || word === "--help") {
      throw new Error(USAGE);
    } else {
      break;
    }
  }
  const name = argv[at];
  if (name === undefined) {
    throw new Error(USAGE);
  }
  const args = argv.slice(at + 1);
  const found = COMMANDS.find((command) => command.name === name);
  if (found === undefined) {
    throw new Error(`unknown command ${JSON.stringify(name)}\n${USAGE}`);
  }
  if (args.length !== found.args) {
    throw new Error(`${name} takes ${found.args} argument(s)\n${USAGE}`);
  }
  return { service, name, args };
}

function request(command: Command): { method: string; path: string; body?: Json } {
  const [first, second] = command.args;
  switch (command.name) {
    case "add-title":
      return {
        method: "POST",
        path: "/titles",
        body: { isbn: first, title: second, author: command.args[2] },
      };
    case "add-copy":
      return { method: "POST", path: "/copies", body: { copy: first, isbn: second } };
    case "add-member":
      return { method: "POST", path: "/members", body: { member: first, name: second } };
    case "copy":
      return { method: "GET", path: `/copies/${seg(first)}` };
    case "lend":
      return { method: "POST", path: "/loans", body: { member: first, copy: second } };
    case "renew":
      return { method: "POST", path: `/loans/${seg(first)}/renewal` };
    case "return":
      return { method: "POST", path: `/loans/${seg(first)}/return` };
    case "loans":
      return { method: "GET", path: `/members/${seg(first)}/loans` };
    case "overdue":
      return { method: "GET", path: "/loans/overdue" };
    default:
      throw new Error(`unknown command ${JSON.stringify(command.name)}`);
  }
}

function said(command: Command, body: Json): string {
  switch (command.name) {
    case "add-title":
      return `cataloged ${textOf(body, "isbn")}: ${textOf(body, "title")} by ${
        textOf(body, "author")
      }`;
    case "add-copy":
      return `added copy ${textOf(body, "copy")} of ${textOf(body, "isbn")}`;
    case "add-member":
      return `registered ${textOf(body, "member")}: ${textOf(body, "name")}`;
    case "copy": {
      const loan = isObject(body) ? body.loan : null;
      const state = isObject(loan) ? `on loan, ${showLoan(loan)}` : "available";
      return `${textOf(body, "copy")} (${textOf(body, "isbn")}): ${state}`;
    }
    case "lend":
      return `lent ${showLoan(body)}`;
    case "renew":
      return `renewed ${showLoan(body)}`;
    case "return": {
      const late = numberOf(body, "days_late");
      const owes = late > 0
        ? `, ${late} day(s) late: owes ${dollars(numberOf(body, "fee_cents"))}`
        : "";
      return `returned ${textOf(body, "copy")} from ${textOf(body, "member")}${owes}`;
    }
    case "loans": {
      const rows = rowsOf(body, "loans").map((row) => {
        const overdue = row.overdue === true
          ? `  OVERDUE ${numberOf(row, "days_late")} day(s)`
          : "";
        return `${textOf(row, "copy").padEnd(8)} due ${textOf(row, "due")}${overdue}  ${
          textOf(row, "title")
        }`;
      });
      return rows.length > 0 ? rows.join("\n") : `${command.args[0]} has no loans`;
    }
    case "overdue": {
      const rows = rowsOf(body, "overdue").map(
        (row) =>
          `${textOf(row, "copy").padEnd(8)} ${textOf(row, "member").padEnd(10)} due ${
            textOf(row, "due")
          }  ` +
          `${numberOf(row, "days_late")} day(s) late`,
      );
      return rows.length > 0 ? rows.join("\n") : "nothing is overdue";
    }
    default:
      throw new Error(`unknown command ${JSON.stringify(command.name)}`);
  }
}

function rowsOf(body: Json, key: string): Array<{ [key: string]: Json }> {
  if (!isObject(body)) {
    return [];
  }
  const rows = body[key];
  return Array.isArray(rows) ? rows.filter(isObject) : [];
}

/**
 * Run one command, writing what it did to `out` and why it failed to `err`. The exit status
 * is 0 done, 1 refused by the service, 2 the service did not answer.
 */
export async function run(
  command: Command,
  client: Client,
  out: { write(text: string): unknown },
  err: { write(text: string): unknown },
): Promise<number> {
  const { method, path, body } = request(command);
  let status: number;
  let answer: Json;
  try {
    [status, answer] = await client.call(method, path, body);
  } catch (e) {
    if (e instanceof Unreachable) {
      err.write(`library: ${e.message}\n`);
      return 2;
    }
    throw e;
  }
  if (status >= 400) {
    err.write(`library: refused (${textOf(answer, "error")}): ${textOf(answer, "message")}\n`);
    return 1;
  }
  out.write(`${said(command, answer)}\n`);
  return 0;
}
