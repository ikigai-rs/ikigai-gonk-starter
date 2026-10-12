/**
 * The command line against the real service, on a free port with a clock the test sets.
 *
 * The CLI speaks the service's JSON, and its tests start the real service in process, on a
 * free port, with a clock the test sets, exactly as the Python and Rust samples' do. The
 * service is the sample's other artifact, so it is imported by relative path, never by a
 * version and never from a registry.
 */

import { Clock, Day } from "../../service/src/date.ts";
import { Library } from "../../service/src/domain.ts";
import { serve, Served } from "../../service/src/http.ts";
import { Client, parse, run } from "../src/lib.ts";

class Cli {
  private clock = new Clock(Day.fromYmd(1970, 1, 1));
  private served: Served;

  constructor(today: string) {
    this.clock.set(day(today));
    this.served = serve(new Library(), this.clock, { port: 0 });
  }

  stop(): Promise<void> {
    return this.served.stop();
  }

  set(today: string): void {
    this.clock.set(day(today));
  }

  /** The command line, as `library --service <url> …` would be parsed, and its run. */
  async cli(argv: string[]): Promise<[number, string, string]> {
    const command = parse(["--service", this.served.base, ...argv]);
    let out = "";
    let err = "";
    const status = await run(
      command,
      new Client(command.service),
      { write: (text: string) => (out += text) },
      { write: (text: string) => (err += text) },
    );
    return [status, out.trimEnd(), err.trimEnd()];
  }

  async shelf(): Promise<void> {
    const lines: string[][] = [
      ["add-title", "9780141439518", "Pride and Prejudice", "Jane Austen"],
      ["add-copy", "pp-1", "9780141439518"],
      ["add-copy", "pp-2", "9780141439518"],
      ["add-member", "ada", "Ada Lovelace"],
      ["add-member", "grace", "Grace Hopper"],
    ];
    for (const argv of lines) {
      const [status, , err] = await this.cli(argv);
      if (status !== 0) {
        throw new Error(`${argv.join(" ")}: ${err}`);
      }
    }
  }
}

Deno.test("cataloging answers in words", async () => {
  const cli = new Cli("2026-10-01");
  assertEquals(
    await cli.cli(["add-title", "9780141439518", "Pride and Prejudice", "Jane Austen"]),
    [0, "cataloged 9780141439518: Pride and Prejudice by Jane Austen", ""],
  );
  await cli.stop();
});

Deno.test("a loan from lending to a late return", async () => {
  const cli = new Cli("2026-10-01");
  await cli.shelf();
  assertEquals((await cli.cli(["lend", "ada", "pp-1"]))[1], "lent pp-1 to ada, due 2026-10-22");
  assertEquals(
    (await cli.cli(["copy", "pp-1"]))[1],
    "pp-1 (9780141439518): on loan, pp-1 to ada, due 2026-10-22",
  );
  assertEquals(
    (await cli.cli(["renew", "pp-1"]))[1],
    "renewed pp-1 to ada, due 2026-11-12, renewed 1x",
  );
  cli.set("2026-11-14");
  const [status, out] = await cli.cli(["loans", "ada"]);
  assertEquals(status, 0);
  assertEquals(out, "pp-1     due 2026-11-12  OVERDUE 2 day(s)  Pride and Prejudice");
  assertEquals(
    (await cli.cli(["overdue"]))[1],
    "pp-1     ada        due 2026-11-12  2 day(s) late",
  );
  assertEquals(
    (await cli.cli(["return", "pp-1"]))[1],
    "returned pp-1 from ada, 2 day(s) late: owes $0.50",
  );
  assertEquals((await cli.cli(["overdue"]))[1], "nothing is overdue");
  assertEquals((await cli.cli(["loans", "ada"]))[1], "ada has no loans");
  await cli.stop();
});

Deno.test("a refusal is exit 1 with the service's code", async () => {
  const cli = new Cli("2026-10-01");
  await cli.shelf();
  await cli.cli(["lend", "ada", "pp-1"]);
  const [status, out, err] = await cli.cli(["lend", "grace", "pp-1"]);
  assertEquals([status, out], [1, ""]);
  if (!err.startsWith("library: refused (copy-on-loan): copy pp-1 is out")) {
    throw new Error(err);
  }
  await cli.stop();
});

Deno.test("no service is exit 2", async () => {
  // A port nothing listens on: bound and then released, so nothing can be racing for it.
  const listener = Deno.listen({ hostname: "127.0.0.1", port: 0 });
  const port = (listener.addr as Deno.NetAddr).port;
  listener.close();
  const command = parse(["--service", `http://127.0.0.1:${port}`, "overdue"]);
  let out = "";
  let err = "";
  const status = await run(
    command,
    new Client(command.service),
    { write: (text: string) => (out += text) },
    { write: (text: string) => (err += text) },
  );
  assertEquals(status, 2);
  if (!err.includes("no answer from")) {
    throw new Error(err);
  }
});

function day(text: string): Day {
  const found = Day.parse(text);
  if (found === null) {
    throw new Error(`${text} is not a date`);
  }
  return found;
}

function assertEquals(got: unknown, want: unknown): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
}
