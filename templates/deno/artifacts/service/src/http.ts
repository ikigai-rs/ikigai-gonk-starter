/**
 * The library's HTTP JSON API: a routing table over `domain`, and nothing else.
 *
 * ```text
 * POST /titles                 {"isbn", "title", "author"}   catalog a title
 * POST /copies                 {"copy", "isbn"}              add a copy of it
 * POST /members                {"member", "name"}            register a member
 * GET  /copies/{copy}                                        a copy, and its loan if out
 * POST /loans                  {"member", "copy"}            lend a copy
 * POST /loans/{copy}/renewal                                 renew its loan
 * POST /loans/{copy}/return                                  return it
 * GET  /members/{member}/loans                               a member's loans
 * GET  /loans/overdue                                        every overdue loan
 * ```
 *
 * A refusal is `{"error": <code>, "message": <text>}` with 404 for something unknown, 400
 * for a malformed request and 409 for a rule. The DATE of every request is the server's
 * clock, never the client's: the clock is an argument of `serve`, so a test hands it a fixed
 * or settable one and `serve --today` pins it for a demonstration.
 *
 * The wire is Deno's own HTTP server (`Deno.serve`), standard library and no dependencies.
 * One thing that server does not do is read a request body it was not asked for, so the
 * body of an operation whose arguments come from it is read here, and a body that is not a
 * JSON object is refused rather than guessed at.
 */

import type { Json } from "./json.ts";
import { isObject } from "./json.ts";
import type { Clock } from "./date.ts";
import { apply, Library, Refused } from "./domain.ts";

/** One route: its method, the path pattern, the operation, and where its arguments come from. */
interface Route {
  method: "GET" | "POST";
  pattern: string;
  op: string;
  fromBody: boolean;
}

const ROUTES: Route[] = [
  { method: "POST", pattern: "/titles", op: "add_title", fromBody: true },
  { method: "POST", pattern: "/copies", op: "add_copy", fromBody: true },
  { method: "POST", pattern: "/members", op: "add_member", fromBody: true },
  { method: "GET", pattern: "/copies/{copy}", op: "copy", fromBody: false },
  { method: "POST", pattern: "/loans", op: "lend", fromBody: true },
  { method: "GET", pattern: "/loans/overdue", op: "overdue", fromBody: false },
  { method: "POST", pattern: "/loans/{copy}/renewal", op: "renew", fromBody: false },
  { method: "POST", pattern: "/loans/{copy}/return", op: "return", fromBody: false },
  { method: "GET", pattern: "/members/{member}/loans", op: "loans", fromBody: false },
];

const CREATES = new Set(["add_title", "add_copy", "add_member", "lend"]);

export const MAX_BODY = 64 * 1024;

function statusFor(code: string): number {
  switch (code) {
    case "unknown-title":
    case "unknown-copy":
    case "unknown-member":
    case "no-route":
      return 404;
    case "bad-request":
      return 400;
    default:
      return 409;
  }
}

/**
 * The path a pattern matches, and what its `{name}` segments took: the whole path, segment
 * by segment, so `/copies/{copy}` takes one segment and nothing across a `/`.
 */
function matchPath(pattern: string, path: string): Record<string, string> | null {
  const want = pattern.split("/");
  const have = path.split("/");
  if (want.length !== have.length) {
    return null;
  }
  const captured: Record<string, string> = {};
  for (let at = 0; at < want.length; at += 1) {
    const wanted = want[at];
    const found = have[at];
    if (wanted.startsWith("{") && wanted.endsWith("}")) {
      if (found === "") {
        return null;
      }
      // The segment as it came: the other targets' servers do not decode either, and the
      // client is the one that percent-encodes an id (`seg` in the CLI).
      captured[wanted.slice(1, -1)] = found;
    } else if (wanted !== found) {
      return null;
    }
  }
  return captured;
}

function refusal(code: string, message: string): Json {
  return { error: code, message };
}

/** What one request answers: the status, and the JSON body. */
async function dispatch(request: Request, library: Library, clock: Clock): Promise<[number, Json]> {
  const path = new URL(request.url).pathname;
  for (const route of ROUTES) {
    if (route.method !== request.method) {
      continue;
    }
    const captured = matchPath(route.pattern, path);
    if (captured === null) {
      continue;
    }
    let args: Json;
    if (route.fromBody) {
      const text = await request.text();
      let parsed: unknown;
      try {
        parsed = text === "" ? {} : JSON.parse(text);
      } catch (e) {
        return [400, refusal("bad-request", `the body is not JSON: ${e}`)];
      }
      if (!isObject(parsed)) {
        return [400, refusal("bad-request", "the body is a JSON object")];
      }
      args = parsed;
    } else {
      args = captured;
    }
    try {
      const answer = apply(library, route.op, args, clock.today());
      return [CREATES.has(route.op) ? 201 : 200, answer];
    } catch (e) {
      if (e instanceof Refused) {
        return [statusFor(e.code), refusal(e.code, e.message)];
      }
      throw e;
    }
  }
  return [404, refusal("no-route", `no route ${request.method} ${path}`)];
}

/** A running server, and the address it answers on. */
export interface Served {
  /** `http://host:port` */
  base: string;
  /** Stop it, and let it go. */
  stop(): Promise<void>;
}

/**
 * Serve `library`, dating every request with `clock`. Port 0 picks a free one, and the
 * server is listening (its address known) before this answers.
 */
export function serve(
  library: Library,
  clock: Clock,
  options: { hostname?: string; port?: number; verbose?: boolean } = {},
): Served {
  const hostname = options.hostname ?? "127.0.0.1";
  const port = options.port ?? 8080;
  const verbose = options.verbose ?? false;
  const server = Deno.serve({
    hostname,
    port,
    onListen: () => {},
    handler: async (request) => {
      // A body larger than the limit is refused before it is read.
      const tooBig = request.headers.get("content-length") ?? "0";
      if (Number(tooBig) > MAX_BODY) {
        return Response.json(refusal("bad-request", `a body is at most ${MAX_BODY} bytes`), {
          status: 400,
        });
      }
      const [status, body] = await dispatch(request, library, clock);
      if (verbose) {
        console.error(`${request.method} ${new URL(request.url).pathname} -> ${status}`);
      }
      return Response.json(body, { status });
    },
  });
  const address = server.addr as Deno.NetAddr;
  return {
    base: `http://${address.hostname}:${address.port}`,
    stop: () => server.shutdown(),
  };
}
