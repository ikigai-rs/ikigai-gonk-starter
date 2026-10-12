//! The spec's scenarios, run as tests: every entry of openspec/vectors.json, twice.
//!
//! Once against the rules directly (`domain::apply`) and once over HTTP against a real server
//! on a free port, whose clock the test moves to each step's date. The vector file is the
//! language-neutral half: every language's target runs the same file, so no target can say
//! something different from the spec without a test failing.

mod common;

use std::cell::RefCell;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Arc;

use lending_service::date::{Clock, Date};
use lending_service::domain::{apply, Library, Refused};
use lending_service::http::Server;
use lending_service::json::{self, Value};

use common::{http_request, steps_of, vectors};

/// What one step answers: the refusal's code, or the whole answer.
type Answered = (Option<String>, Option<Value>);

trait Runner {
    fn run(&self, op: &str, args: &Value, today: Date) -> Answered;
}

/// Runs a step against the rules, in process.
struct Direct {
    library: RefCell<Library>,
}

impl Direct {
    fn new() -> Direct {
        Direct {
            library: RefCell::new(Library::new()),
        }
    }
}

impl Runner for Direct {
    fn run(&self, op: &str, args: &Value, today: Date) -> Answered {
        match apply(&mut self.library.borrow_mut(), op, args, today) {
            Ok(answer) => (None, Some(answer)),
            Err(Refused { code, .. }) => (Some(code), None),
        }
    }
}

/// Runs a step against a real server, after setting its clock to the step's date.
struct OverHttp {
    base: String,
    clock: Arc<Clock>,
    /// Held so the accept loop lives as long as the runner does; dropped with it.
    _server: Server,
}

impl OverHttp {
    fn new() -> OverHttp {
        let clock = Arc::new(Clock::fixed(Date::from_ymd(1970, 1, 1)));
        let server = Server::start(Library::new(), Arc::clone(&clock), "127.0.0.1", 0, false)
            .expect("a free port");
        OverHttp {
            base: server.base(),
            clock,
            _server: server,
        }
    }
}

impl Runner for OverHttp {
    fn run(&self, op: &str, args: &Value, today: Date) -> Answered {
        self.clock.set(today);
        let (method, path, body) = http_request(op, args);
        let (status, answer) = request(&self.base, method, &path, body.as_ref());
        if status >= 400 {
            let code = answer
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            (Some(code), None)
        } else {
            (None, Some(answer))
        }
    }
}

/// One request by hand: one request per connection, `Connection: close`, the answer read to
/// the end. (The CLI's `Client` does the same, but a test that used it could not tell a bug in
/// one from a bug in the other.)
fn request(base: &str, method: &str, path: &str, body: Option<&Value>) -> (u16, Value) {
    let authority = base.strip_prefix("http://").expect("a base URL");
    let (host, port) = authority.split_once(':').expect("host:port");
    let mut stream = TcpStream::connect((host, port.parse::<u16>().expect("a port")))
        .expect("the service is listening");
    let payload = body.map(json::to_text).unwrap_or_default();
    let head = format!(
        "{method} {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        payload.len()
    );
    stream.write_all(head.as_bytes()).unwrap();
    stream.write_all(payload.as_bytes()).unwrap();
    stream.flush().unwrap();
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).unwrap();
    let text = String::from_utf8_lossy(&raw);
    let (head, payload) = text.split_once("\r\n\r\n").expect("a header and a body");
    let status: u16 = head
        .split_whitespace()
        .nth(1)
        .expect("a status")
        .parse()
        .expect("a number");
    (status, json::parse(payload).expect("a JSON body"))
}

fn check(make_runner: impl Fn() -> Box<dyn Runner>) {
    let vectors = vectors();
    let base = vectors
        .get("today")
        .and_then(Value::as_str)
        .expect("a today");
    let scenarios = vectors
        .get("scenarios")
        .and_then(Value::as_array)
        .expect("scenarios");
    let mut ran = 0;
    for scenario in scenarios {
        let runner = make_runner();
        let mut today = Date::parse(base).expect("today is a date");
        for (n, step) in steps_of(&vectors, scenario).iter().enumerate() {
            if let Some(on) = step.get("on").and_then(Value::as_str) {
                today = Date::parse(on).expect("a step's date");
            }
            let op = step.get("op").and_then(Value::as_str).expect("an op");
            let args = step
                .get("args")
                .cloned()
                .unwrap_or(Value::Object(Default::default()));
            let where_ = format!(
                "{} step {n} ({op} {args:?} on {})",
                scenario.get("id").and_then(Value::as_str).unwrap_or(""),
                today.to_iso()
            );
            let (refused, answer) = runner.run(op, &args, today);
            if let Some(wanted) = step.get("refused").and_then(Value::as_str) {
                assert_eq!(refused.as_deref(), Some(wanted), "{where_}");
                continue;
            }
            assert_eq!(refused, None, "{where_}: refused");
            if let Some(wanted) = step.get("expect") {
                assert_eq!(answer.as_ref(), Some(wanted), "{where_}");
            }
            ran += 1;
        }
    }
    assert!(ran > 0, "no scenario ran: the vectors were not read");
}

#[test]
fn every_scenario_against_the_rules() {
    check(|| Box::new(Direct::new()));
}

#[test]
fn every_scenario_over_http() {
    check(|| Box::new(OverHttp::new()));
}
