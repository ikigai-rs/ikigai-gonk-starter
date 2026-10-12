//! The library's command line: a client of the service's HTTP JSON API (see `lending_cli`).

use std::process::ExitCode;

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let command = match lending_cli::parse(&argv) {
        Ok(command) => command,
        Err(why) => {
            eprintln!("library: {why}");
            return ExitCode::from(2);
        }
    };
    let client = lending_cli::Client::new(&command.service);
    let mut out = std::io::stdout();
    let mut err = std::io::stderr();
    ExitCode::from(lending_cli::run(&command, &client, &mut out, &mut err))
}
