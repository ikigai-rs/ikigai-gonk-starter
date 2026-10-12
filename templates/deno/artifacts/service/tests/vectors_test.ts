/**
 * The spec's scenarios, run as tests: every entry of openspec/vectors.json, twice.
 *
 * Once against the rules directly (`domain.apply`) and once over HTTP against a real server
 * on a free port, whose clock the test moves to each step's date. The vector file is the
 * language-neutral half: every language's target runs the same file, so no target can say
 * something different from the spec without a test failing.
 */

import { Clock, Day } from "../src/date.ts";
import { apply, Library, Refused } from "../src/domain.ts";
import { serve, Served } from "../src/http.ts";
import type { Json } from "../src/json.ts";
import { isObject } from "../src/json.ts";
import { httpRequest, stepsOf, vectors } from "./common.ts";

/** What one step answers: the refusal's code, or the whole answer. */
type Answered = { refused: string | null; answer: Json | null };

interface Runner {
  run(op: string, args: Json, today: Day): Promise<Answered> | Answered;
  stop?(): Promise<void>;
}

/** Runs a step against the rules, in process. */
class Direct implements Runner {
  private library = new Library();

  run(op: string, args: Json, today: Day): Answered {
    try {
      return { refused: null, answer: apply(this.library, op, args, today) };
    } catch (e) {
      if (e instanceof Refused) {
        return { refused: e.code, answer: null };
      }
      throw e;
    }
  }
}

/** Runs a step against a real server, after setting its clock to the step's date. */
class OverHttp implements Runner {
  private clock = new Clock(Day.fromYmd(1970, 1, 1));
  private served: Served;

  constructor() {
    this.served = serve(new Library(), this.clock, { port: 0 });
  }

  async run(op: string, args: Json, today: Day): Promise<Answered> {
    this.clock.set(today);
    const { method, path, body } = httpRequest(op, args);
    const response = await fetch(this.served.base + path, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
      headers: body === undefined ? undefined : { "content-type": "application/json" },
    });
    const answer: Json = JSON.parse(await response.text());
    if (response.status >= 400) {
      const code = isObject(answer) && typeof answer.error === "string" ? answer.error : "";
      return { refused: code, answer: null };
    }
    return { refused: null, answer };
  }

  stop(): Promise<void> {
    return this.served.stop();
  }
}

function dateOf(value: Json, where: string): Day {
  const date = typeof value === "string" ? Day.parse(value) : null;
  if (date === null) {
    throw new Error(`${where}: not a date`);
  }
  return date;
}

async function check(makeRunner: () => Runner): Promise<void> {
  const file = await vectors();
  const base: Json = isObject(file) ? file.today ?? null : null;
  const scenarios = isObject(file) && Array.isArray(file.scenarios) ? file.scenarios : [];
  let ran = 0;
  for (const scenario of scenarios) {
    const runner = makeRunner();
    let today = dateOf(base, "today");
    const steps = stepsOf(file, scenario);
    for (let n = 0; n < steps.length; n += 1) {
      const step = steps[n];
      if (!isObject(step)) {
        continue;
      }
      if (typeof step.on === "string") {
        today = dateOf(step.on, "a step's date");
      }
      const op = String(step.op);
      const args: Json = step.args ?? {};
      const id = isObject(scenario) ? String(scenario.id) : "";
      const where = `${id} step ${n} (${op} ${JSON.stringify(args)} on ${today.iso()})`;
      const { refused, answer } = await runner.run(op, args, today);
      if (typeof step.refused === "string") {
        assertEquals(refused, step.refused, where);
        continue;
      }
      assertEquals(refused, null, `${where}: refused`);
      if (step.expect !== undefined) {
        assertEquals(answer, step.expect, where);
      }
      ran += 1;
    }
    await runner.stop?.();
  }
  if (ran === 0) {
    throw new Error("no scenario ran: the vectors were not read");
  }
}

/** Deep equality of JSON values, with a message that names the step. */
function assertEquals(got: Json, want: Json, where: string): void {
  const a = canonical(got);
  const b = canonical(want);
  if (a !== b) {
    throw new Error(`${where}\n  got:  ${a}\n  want: ${b}`);
  }
}

/** One JSON value in a canonical form: object members sorted, so two objects with the same
 * members compare equal whatever order they were written in. */
function canonical(value: Json): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  if (isObject(value)) {
    const members = Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`);
    return `{${members.join(",")}}`;
  }
  return JSON.stringify(value);
}

Deno.test("every scenario against the rules", async () => {
  await check(() => new Direct());
});

Deno.test("every scenario over HTTP", async () => {
  await check(() => new OverHttp());
});
