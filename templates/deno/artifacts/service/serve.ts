/**
 * Run the library-lending service.
 *
 * ```text
 * deno run --allow-net artifacts/service/serve.ts [--host 127.0.0.1] [--port 8080]
 *                                                [--today 2026-10-01] [--verbose]
 * ```
 *
 * The state is in memory and goes when the process does. `--today` pins the server's clock
 * to one date, so a demonstration or a book chapter gives the same due dates on any day it
 * runs; without it the server dates each request with today, counted in UTC days.
 *
 * The server runs until the process is interrupted: `Deno.serve` holds the event loop open,
 * so `main` sets it going and returns; only a refusal exits here.
 */

import { Clock, Day } from "./src/date.ts";
import { Library } from "./src/domain.ts";
import { serve } from "./src/http.ts";

const USAGE = "usage: serve [--host HOST] [--port PORT] [--today YYYY-MM-DD] [--verbose]";

interface Args {
  host: string;
  port: number;
  today: Day | null;
  verbose: boolean;
}

function parse(argv: string[]): Args {
  const args: Args = { host: "127.0.0.1", port: 8080, today: null, verbose: false };
  for (let at = 0; at < argv.length; at += 1) {
    const flag = argv[at];
    const value = (): string => {
      at += 1;
      if (at >= argv.length) {
        throw new Error(`${flag} needs a value`);
      }
      return argv[at];
    };
    switch (flag) {
      case "--host":
        args.host = value();
        break;
      case "--port": {
        const text = value();
        const port = Number(text);
        if (!Number.isInteger(port) || port < 0 || port > 65_535) {
          throw new Error("--port is a number, 0 to 65535");
        }
        args.port = port;
        break;
      }
      case "--today": {
        const text = value();
        const today = Day.parse(text);
        if (today === null) {
          throw new Error(`--today ${JSON.stringify(text)} is not YYYY-MM-DD`);
        }
        args.today = today;
        break;
      }
      case "--verbose":
        args.verbose = true;
        break;
      case "-h":
      case "--help":
        throw new Error(USAGE);
      default:
        throw new Error(`unknown argument ${JSON.stringify(flag)}\n${USAGE}`);
    }
  }
  return args;
}

function main(): number {
  let args: Args;
  try {
    args = parse(Deno.args);
  } catch (e) {
    console.error(`serve: ${(e as Error).message}`);
    return 2;
  }
  const clock = new Clock(args.today ?? Day.today());
  let server;
  try {
    server = serve(new Library(), clock, {
      hostname: args.host,
      port: args.port,
      verbose: args.verbose,
    });
  } catch (e) {
    console.error(`serve: cannot listen on ${args.host}:${args.port}: ${e}`);
    return 2;
  }
  const pinned = args.today === null ? "" : `, clock pinned to ${args.today.iso()}`;
  console.log(`library service on ${server.base}${pinned}`);
  // Deno.serve keeps this process alive until it is interrupted; nothing else to do here.
  return 0;
}

const status = main();
if (status !== 0) {
  Deno.exit(status);
}
