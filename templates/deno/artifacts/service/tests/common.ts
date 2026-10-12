/**
 * Shared test support: the scenario vectors, and the HTTP form of each operation.
 */

import type { Json } from "../src/json.ts";
import { isObject } from "../src/json.ts";

/** The repository root, from this file's URL: the tests are artifacts/service/tests. */
export function repoRoot(): URL {
  return new URL("../../../", import.meta.url);
}

/** `openspec/vectors.json`, the language-neutral half of the spec. */
export async function vectors(): Promise<Json> {
  const text = await Deno.readTextFile(new URL("openspec/vectors.json", repoRoot()));
  return JSON.parse(text);
}

/** The fixtures a scenario names, then its own steps. */
export function stepsOf(vectors: Json, scenario: Json): Json[] {
  const fixtures = isObject(vectors) && isObject(vectors.fixtures) ? vectors.fixtures : {};
  const steps: Json[] = [];
  const given = isObject(scenario) ? scenario.given : undefined;
  if (Array.isArray(given)) {
    for (const name of given) {
      const fixture = fixtures[String(name)];
      if (!Array.isArray(fixture)) {
        throw new Error(`no fixture ${name}`);
      }
      steps.push(...fixture);
    }
  }
  const own = isObject(scenario) ? scenario.steps : undefined;
  if (Array.isArray(own)) {
    steps.push(...own);
  }
  return steps;
}

/** How each operation is asked over HTTP: its method, its path, and the body if any. */
export function httpRequest(
  op: string,
  args: Json,
): { method: string; path: string; body?: Json } {
  const text = (key: string): string => {
    const found = isObject(args) ? args[key] : undefined;
    if (typeof found !== "string") {
      throw new Error(`${op} needs ${key}`);
    }
    return found;
  };
  switch (op) {
    case "add_title":
      return { method: "POST", path: "/titles", body: args };
    case "add_copy":
      return { method: "POST", path: "/copies", body: args };
    case "add_member":
      return { method: "POST", path: "/members", body: args };
    case "lend":
      return { method: "POST", path: "/loans", body: args };
    case "copy":
      return { method: "GET", path: `/copies/${encodeURIComponent(text("copy"))}` };
    case "renew":
      return { method: "POST", path: `/loans/${encodeURIComponent(text("copy"))}/renewal` };
    case "return":
      return { method: "POST", path: `/loans/${encodeURIComponent(text("copy"))}/return` };
    case "loans":
      return { method: "GET", path: `/members/${encodeURIComponent(text("member"))}/loans` };
    case "overdue":
      return { method: "GET", path: "/loans/overdue" };
    default:
      throw new Error(`no HTTP form for ${op}`);
  }
}
