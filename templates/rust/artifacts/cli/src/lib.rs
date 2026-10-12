//! The library's command line: a client of the service's HTTP JSON API.
//!
//! ```text
//! library [--service URL] <command> …
//!
//! add-title ISBN TITLE AUTHOR    catalog a title
//! add-copy COPY ISBN             add a copy of it
//! add-member MEMBER NAME         register a member
//! copy COPY                      a copy, and its loan if it is out
//! lend MEMBER COPY               lend a copy
//! renew COPY                     renew its loan
//! return COPY                    return it
//! loans MEMBER                   a member's loans
//! overdue                        every overdue loan
//! ```
//!
//! The service is `http://127.0.0.1:8080` unless `--service` names another. The client never
//! sends a date: the service's clock dates every request. Exit status: 0 done, 1 refused by
//! the service (its code and message on stderr), 2 the service could not be reached or the
//! command line is wrong.
//!
//! The HTTP client is by hand, on the standard library's TCP, so this artifact has no
//! dependencies either: one request per connection, `Connection: close`, and the answer read
//! to the end.

use std::io::{Read, Write};
use std::net::TcpStream;

use lending_service::json::{self, Value};

pub const DEFAULT_SERVICE: &str = "http://127.0.0.1:8080";
const TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);

/// The service could not be reached, or answered something that is not an answer.
#[derive(Debug)]
pub struct Unreachable(pub String);

impl std::fmt::Display for Unreachable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

pub struct Client {
    base: String,
}

impl Client {
    pub fn new(base: &str) -> Client {
        Client {
            base: base.trim_end_matches('/').to_string(),
        }
    }

    /// One request, answered as (status, body).
    pub fn call(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
    ) -> Result<(u16, Value), Unreachable> {
        let (host, port) = self.address()?;
        let mut stream = TcpStream::connect((host.as_str(), port))
            .map_err(|e| Unreachable(format!("no answer from {}: {e}", self.base)))?;
        let _ = stream.set_read_timeout(Some(TIMEOUT));
        let _ = stream.set_write_timeout(Some(TIMEOUT));

        let payload = match body {
            Some(value) => json::to_text(value),
            None => String::new(),
        };
        let request = format!(
            "{method} {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
            payload.len()
        );
        stream
            .write_all(request.as_bytes())
            .and_then(|()| stream.flush())
            .map_err(|e| Unreachable(format!("no answer from {}: {e}", self.base)))?;

        let mut raw = Vec::new();
        stream
            .read_to_end(&mut raw)
            .map_err(|e| Unreachable(format!("no answer from {}: {e}", self.base)))?;
        let text = String::from_utf8_lossy(&raw);
        let (head, payload) = text.split_once("\r\n\r\n").unwrap_or((text.as_ref(), ""));
        let status = head
            .split_whitespace()
            .nth(1)
            .and_then(|code| code.parse::<u16>().ok())
            .ok_or_else(|| Unreachable(format!("no answer from {}: no status line", self.base)))?;
        match json::parse(payload) {
            Ok(value) => Ok((status, value)),
            Err(e) => Ok((
                status,
                Value::object(vec![
                    ("error", Value::string(&format!("http-{status}"))),
                    ("message", Value::string(&e)),
                ]),
            )),
        }
    }

    /// The host and port of `base`, which must be an `http://host[:port]` URL.
    fn address(&self) -> Result<(String, u16), Unreachable> {
        let rest = self.base.strip_prefix("http://").ok_or_else(|| {
            Unreachable(format!("no answer from {}: not an http:// URL", self.base))
        })?;
        let authority = rest.split('/').next().unwrap_or(rest);
        let (host, port) = match authority.rsplit_once(':') {
            Some((host, port)) => (
                host.to_string(),
                port.parse::<u16>()
                    .map_err(|_| Unreachable(format!("no answer from {}: bad port", self.base)))?,
            ),
            None => (authority.to_string(), 80),
        };
        Ok((host, port))
    }
}

/// One path segment, so an id with a `/` or a space stays one segment.
pub fn seg(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}

fn dollars(cents: i64) -> String {
    format!("${}.{:02}", cents / 100, cents % 100)
}

fn show_loan(loan: &Value) -> String {
    let renewals = loan.get("renewals").and_then(Value::as_i64).unwrap_or(0);
    let renewed = if renewals > 0 {
        format!(", renewed {renewals}x")
    } else {
        String::new()
    };
    format!(
        "{} to {}, due {}{renewed}",
        text_of(loan, "copy"),
        text_of(loan, "member"),
        text_of(loan, "due")
    )
}

fn text_of(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

/// A command line, already parsed: the command, its arguments, and where the service is.
#[derive(Debug)]
pub struct Command {
    pub service: String,
    pub name: String,
    pub args: Vec<String>,
}

const COMMANDS: &[(&str, usize, &str)] = &[
    ("add-title", 3, "catalog a title"),
    ("add-copy", 2, "add a copy of a cataloged title"),
    ("add-member", 2, "register a member"),
    ("copy", 1, "a copy, and its loan if it is out"),
    ("lend", 2, "lend a copy to a member"),
    ("renew", 1, "renew a loan"),
    ("return", 1, "return a copy"),
    ("loans", 1, "a member's loans"),
    ("overdue", 0, "every overdue loan"),
];

pub const USAGE: &str = "usage: library [--service URL] <command> …\n  add-title ISBN TITLE AUTHOR\n  add-copy COPY ISBN\n  add-member MEMBER NAME\n  copy COPY\n  lend MEMBER COPY\n  renew COPY\n  return COPY\n  loans MEMBER\n  overdue";

pub fn parse(argv: &[String]) -> Result<Command, String> {
    let mut service = DEFAULT_SERVICE.to_string();
    let mut rest = argv.iter().peekable();
    while let Some(word) = rest.peek() {
        if *word == "--service" {
            rest.next();
            service = rest.next().ok_or("--service needs a URL")?.clone();
        } else if *word == "-h" || *word == "--help" {
            return Err(USAGE.to_string());
        } else {
            break;
        }
    }
    let name = rest.next().ok_or_else(|| USAGE.to_string())?.clone();
    let args: Vec<String> = rest.cloned().collect();
    let (_, wanted, _) = COMMANDS
        .iter()
        .find(|(command, _, _)| *command == name)
        .ok_or_else(|| format!("unknown command {name:?}\n{USAGE}"))?;
    if args.len() != *wanted {
        return Err(format!("{name} takes {wanted} argument(s)\n{USAGE}"));
    }
    Ok(Command {
        service,
        name,
        args,
    })
}

/// Run one command, writing what it did to `out` and why it failed to `err`. The exit status
/// is 0 done, 1 refused by the service, 2 the service did not answer.
pub fn run(command: &Command, client: &Client, out: &mut dyn Write, err: &mut dyn Write) -> u8 {
    let args = &command.args;
    let call =
        |method: &str, path: &str, body: Option<Value>| client.call(method, path, body.as_ref());
    let asked = match command.name.as_str() {
        "add-title" => call(
            "POST",
            "/titles",
            Some(Value::object(vec![
                ("isbn", Value::string(&args[0])),
                ("title", Value::string(&args[1])),
                ("author", Value::string(&args[2])),
            ])),
        ),
        "add-copy" => call(
            "POST",
            "/copies",
            Some(Value::object(vec![
                ("copy", Value::string(&args[0])),
                ("isbn", Value::string(&args[1])),
            ])),
        ),
        "add-member" => call(
            "POST",
            "/members",
            Some(Value::object(vec![
                ("member", Value::string(&args[0])),
                ("name", Value::string(&args[1])),
            ])),
        ),
        "copy" => call("GET", &format!("/copies/{}", seg(&args[0])), None),
        "lend" => call(
            "POST",
            "/loans",
            Some(Value::object(vec![
                ("member", Value::string(&args[0])),
                ("copy", Value::string(&args[1])),
            ])),
        ),
        "renew" => call("POST", &format!("/loans/{}/renewal", seg(&args[0])), None),
        "return" => call("POST", &format!("/loans/{}/return", seg(&args[0])), None),
        "loans" => call("GET", &format!("/members/{}/loans", seg(&args[0])), None),
        "overdue" => call("GET", "/loans/overdue", None),
        other => {
            let _ = writeln!(err, "library: unknown command {other:?}");
            return 2;
        }
    };
    let (status, body) = match asked {
        Ok(answer) => answer,
        Err(unreachable) => {
            let _ = writeln!(err, "library: {unreachable}");
            return 2;
        }
    };
    if status >= 400 {
        let _ = writeln!(
            err,
            "library: refused ({}): {}",
            text_of(&body, "error"),
            text_of(&body, "message")
        );
        return 1;
    }
    let said = match command.name.as_str() {
        "add-title" => format!(
            "cataloged {}: {} by {}",
            text_of(&body, "isbn"),
            text_of(&body, "title"),
            text_of(&body, "author")
        ),
        "add-copy" => format!(
            "added copy {} of {}",
            text_of(&body, "copy"),
            text_of(&body, "isbn")
        ),
        "add-member" => {
            format!(
                "registered {}: {}",
                text_of(&body, "member"),
                text_of(&body, "name")
            )
        }
        "copy" => {
            let loan = body
                .get("loan")
                .filter(|value| !matches!(value, Value::Null));
            let state = match loan {
                Some(loan) => format!("on loan, {}", show_loan(loan)),
                None => "available".to_string(),
            };
            format!(
                "{} ({}): {state}",
                text_of(&body, "copy"),
                text_of(&body, "isbn")
            )
        }
        "lend" => format!("lent {}", show_loan(&body)),
        "renew" => format!("renewed {}", show_loan(&body)),
        "return" => {
            let late = body.get("days_late").and_then(Value::as_i64).unwrap_or(0);
            let owes = if late > 0 {
                format!(
                    ", {late} day(s) late: owes {}",
                    dollars(body.get("fee_cents").and_then(Value::as_i64).unwrap_or(0))
                )
            } else {
                String::new()
            };
            format!(
                "returned {} from {}{owes}",
                text_of(&body, "copy"),
                text_of(&body, "member")
            )
        }
        "loans" => {
            let rows: Vec<String> = body
                .get("loans")
                .and_then(Value::as_array)
                .map(|loans| {
                    loans
                        .iter()
                        .map(|row| {
                            let overdue =
                                if row.get("overdue").and_then(Value::as_bool) == Some(true) {
                                    format!(
                                        "  OVERDUE {} day(s)",
                                        row.get("days_late").and_then(Value::as_i64).unwrap_or(0)
                                    )
                                } else {
                                    String::new()
                                };
                            format!(
                                "{:<8} due {}{overdue}  {}",
                                text_of(row, "copy"),
                                text_of(row, "due"),
                                text_of(row, "title")
                            )
                        })
                        .collect()
                })
                .unwrap_or_default();
            if rows.is_empty() {
                format!("{} has no loans", args[0])
            } else {
                rows.join("\n")
            }
        }
        "overdue" => {
            let rows: Vec<String> = body
                .get("overdue")
                .and_then(Value::as_array)
                .map(|loans| {
                    loans
                        .iter()
                        .map(|row| {
                            format!(
                                "{:<8} {:<10} due {}  {} day(s) late",
                                text_of(row, "copy"),
                                text_of(row, "member"),
                                text_of(row, "due"),
                                row.get("days_late").and_then(Value::as_i64).unwrap_or(0)
                            )
                        })
                        .collect()
                })
                .unwrap_or_default();
            if rows.is_empty() {
                "nothing is overdue".to_string()
            } else {
                rows.join("\n")
            }
        }
        other => format!("{other} did nothing"),
    };
    let _ = writeln!(out, "{said}");
    0
}
