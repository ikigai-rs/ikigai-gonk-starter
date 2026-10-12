/**
 * The library's command line: a client of the service's HTTP JSON API (see `src/lib.ts`).
 */

import { Client, parse, run } from "./src/lib.ts";

const encoder = new TextEncoder();
const out = { write: (text: string) => Deno.stdout.writeSync(encoder.encode(text)) };
const err = { write: (text: string) => Deno.stderr.writeSync(encoder.encode(text)) };

let command;
try {
  command = parse(Deno.args);
} catch (e) {
  err.write(`library: ${(e as Error).message}\n`);
  Deno.exit(2);
}

Deno.exit(await run(command, new Client(command.service), out, err));
