/**
 * What the HTTP API adds to the rules: status codes, malformed requests, and the clock.
 */

import { Clock, Day } from "../src/date.ts";
import { Library } from "../src/domain.ts";
import { serve, Served } from "../src/http.ts";
import type { Json } from "../src/json.ts";
import { isObject } from "../src/json.ts";
import { httpRequest, stepsOf, vectors } from "./common.ts";

/** A test server whose clock the test moves, and the URL it answers on. */
class Api {
  readonly clock = new Clock(Day.fromYmd(1970, 1, 1));
  private served: Served;

  constructor() {
    this.served = serve(new Library(), this.clock, { port: 0 });
  }

  base(): string {
    return this.served.base;
  }

  stop(): Promise<void> {
    return this.served.stop();
  }

  /** A request by hand, answering (status, body). */
  async call(method: string, path: string, body?: Json): Promise<[number, Json]> {
    const response = await fetch(this.served.base + path, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
      headers: body === undefined ? undefined : { "content-type": "application/json" },
    });
    return [response.status, JSON.parse(await response.text())];
  }

  /** A request whose body is not JSON at all. */
  async callRaw(method: string, path: string, raw: string): Promise<[number, Json]> {
    const response = await fetch(this.served.base + path, {
      method,
      body: raw,
      headers: { "content-type": "application/json" },
    });
    return [response.status, JSON.parse(await response.text())];
  }
}

function object(members: Record<string, string>): Json {
  return members;
}

function errorOf(body: Json): string {
  return isObject(body) && typeof body.error === "string" ? body.error : "";
}

function messageOf(body: Json): string {
  return isObject(body) && typeof body.message === "string" ? body.message : "";
}

Deno.test("a creation is 201 and a read is 200", async () => {
  const api = new Api();
  const [created] = await api.call(
    "POST",
    "/titles",
    object({ isbn: "1", title: "T", author: "A" }),
  );
  assertEquals(created, 201);
  await api.call("POST", "/copies", object({ copy: "c", isbn: "1" }));
  const [status, body] = await api.call("GET", "/copies/c");
  assertEquals(status, 200);
  assertEquals(isObject(body) ? body.status : null, "available");
  await api.stop();
});

Deno.test("unknown is 404 and a rule is 409", async () => {
  const api = new Api();
  const [status, body] = await api.call("GET", "/copies/nope");
  assertEquals(status, 404);
  assertEquals(errorOf(body), "unknown-copy");
  const title = object({ isbn: "1", title: "T", author: "A" });
  await api.call("POST", "/titles", title);
  const [again, refused] = await api.call("POST", "/titles", title);
  assertEquals(again, 409);
  assertEquals(errorOf(refused), "duplicate-title");
  await api.stop();
});

Deno.test("a malformed request is 400 and names what is wrong", async () => {
  const api = new Api();
  const [status, body] = await api.callRaw("POST", "/titles", "{not json");
  assertEquals(status, 400);
  assertEquals(errorOf(body), "bad-request");
  const [short, told] = await api.call("POST", "/titles", object({ isbn: "1" }));
  assertEquals(short, 400);
  if (!messageOf(told).includes("title, author")) {
    throw new Error(messageOf(told));
  }
  const [extra, said] = await api.call(
    "POST",
    "/members",
    object({ member: "m", name: "N", age: "9" }),
  );
  assertEquals(extra, 400);
  if (!messageOf(said).includes("takes no age")) {
    throw new Error(messageOf(said));
  }
  await api.stop();
});

Deno.test("an unknown route is 404", async () => {
  const api = new Api();
  const [status, body] = await api.call("POST", "/loans/overdue");
  assertEquals(status, 404);
  assertEquals(errorOf(body), "no-route");
  const [other, missing] = await api.call("GET", "/shelves");
  assertEquals(other, 404);
  assertEquals(errorOf(missing), "no-route");
  await api.stop();
});

Deno.test("the date is the server's clock, not the client's", async () => {
  const api = new Api();
  await api.call("POST", "/titles", object({ isbn: "1", title: "T", author: "A" }));
  await api.call("POST", "/copies", object({ copy: "c", isbn: "1" }));
  await api.call("POST", "/members", object({ member: "m", name: "N" }));
  api.clock.set(Day.fromYmd(2026, 12, 25));
  const [status, body] = await api.call("POST", "/loans", object({ member: "m", copy: "c" }));
  assertEquals(status, 201);
  assertEquals(isObject(body) ? body.lent : null, "2026-12-25");
  assertEquals(isObject(body) ? body.due : null, "2027-01-15");
  await api.stop();
});

Deno.test("the routes agree with the vector forms", async () => {
  // A guard on the test support itself: every operation a vector names has an HTTP form,
  // with the method that form implies.
  const file = await vectors();
  const scenarios = isObject(file) && Array.isArray(file.scenarios) ? file.scenarios : [];
  const seen: string[] = [];
  for (const scenario of scenarios) {
    for (const step of stepsOf(file, scenario)) {
      if (!isObject(step) || typeof step.op !== "string") {
        continue;
      }
      const op = step.op;
      if (seen.includes(op)) {
        continue;
      }
      const { method, path, body } = httpRequest(op, step.args ?? {});
      const reads = op === "copy" || op === "loans" || op === "overdue";
      const writesABody = op === "add_title" || op === "add_copy" || op === "add_member" ||
        op === "lend";
      assertEquals(method, reads ? "GET" : "POST");
      assertEquals(body !== undefined, writesABody);
      if (!path.startsWith("/")) {
        throw new Error(`${op}: ${path}`);
      }
      seen.push(op);
    }
  }
  assertEquals(seen.length, 9);
});

function assertEquals(got: unknown, want: unknown): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
}
