//! Run the library-lending service.
//!
//! ```text
//! serve [--host 127.0.0.1] [--port 8080] [--today 2026-10-01] [--verbose]
//! ```
//!
//! The state is in memory and goes when the process does. `--today` pins the server's clock
//! to one date, so a demonstration or a book chapter gives the same due dates on any day it
//! runs; without it the server dates each request with today, counted in UTC days.

use std::process::ExitCode;
use std::sync::Arc;

use lending_service::date::{Clock, Date};
use lending_service::domain::Library;
use lending_service::http::Server;

const USAGE: &str = "usage: serve [--host HOST] [--port PORT] [--today YYYY-MM-DD] [--verbose]";

struct Args {
    host: String,
    port: u16,
    today: Option<Date>,
    verbose: bool,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut args = Args {
        host: "127.0.0.1".to_string(),
        port: 8080,
        today: None,
        verbose: false,
    };
    let mut rest = argv.iter();
    while let Some(flag) = rest.next() {
        let mut value = || rest.next().ok_or_else(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--host" => args.host = value()?.clone(),
            "--port" => {
                args.port = value()?
                    .parse()
                    .map_err(|_| "--port is a number, 0 to 65535".to_string())?
            }
            "--today" => {
                let text = value()?;
                args.today = Some(
                    Date::parse(text)
                        .ok_or_else(|| format!("--today {text:?} is not YYYY-MM-DD"))?,
                );
            }
            "--verbose" => args.verbose = true,
            "-h" | "--help" => return Err(USAGE.to_string()),
            other => return Err(format!("unknown argument {other:?}\n{USAGE}")),
        }
    }
    Ok(args)
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = match parse(&argv) {
        Ok(args) => args,
        Err(why) => {
            eprintln!("serve: {why}");
            return ExitCode::from(2);
        }
    };
    let clock = Arc::new(Clock::fixed(args.today.unwrap_or_else(Date::today)));
    let server = match Server::start(Library::new(), clock, &args.host, args.port, args.verbose) {
        Ok(server) => server,
        Err(e) => {
            eprintln!("serve: cannot listen on {}:{}: {e}", args.host, args.port);
            return ExitCode::from(2);
        }
    };
    let pinned = match args.today {
        Some(today) => format!(", clock pinned to {}", today.to_iso()),
        None => String::new(),
    };
    println!("library service on {}{pinned}", server.base());
    // The server runs until this process is interrupted; dropping it stops the accept loop.
    let (stop_tx, stop_rx) = std::sync::mpsc::channel::<()>();
    let _ = ctrlc_like(stop_tx);
    let _ = stop_rx.recv();
    drop(server);
    ExitCode::SUCCESS
}

/// A channel that never sends: the process ends on a signal, and this keeps `main` alive
/// without a busy loop. (A signal handler would need a dependency or unsafe code; the
/// process's default behaviour on SIGINT already ends it.)
fn ctrlc_like(_tx: std::sync::mpsc::Sender<()>) -> std::io::Result<()> {
    Ok(())
}
