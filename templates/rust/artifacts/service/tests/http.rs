//! What the HTTP API adds to the rules: status codes, malformed requests, and the clock.

mod common;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Arc;

use lending_service::date::{Clock, Date};
use lending_service::domain::Library;
use lending_service::http::Server;
use lending_service::json::{self, Value};

use common::http_request;

/// A test server whose clock the test moves, and the URL it answers on.
struct Api {
    base: String,
    clock: Arc<Clock>,
    _server: Server,
}

impl Api {
    fn new(today: &str) -> Api {
        let clock = Arc::new(Clock::fixed(Date::parse(today).expect("a date")));
        let server = Server::start(Library::new(), Arc::clone(&clock), "127.0.0.1", 0, false)
            .expect("a free port");
        Api {
            base: server.base(),
            clock,
            _server: server,
        }
    }

    /// A request by hand, answering (status, body).
    fn call(&self, method: &str, path: &str, body: Option<&Value>) -> (u16, Value) {
        let authority = self.base.strip_prefix("http://").expect("a base URL");
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

    /// A request whose body is not JSON at all.
    fn call_raw(&self, method: &str, path: &str, raw: &str) -> (u16, Value) {
        let authority = self.base.strip_prefix("http://").expect("a base URL");
        let (host, port) = authority.split_once(':').expect("host:port");
        let mut stream = TcpStream::connect((host, port.parse::<u16>().expect("a port"))).unwrap();
        let head = format!(
            "{method} {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            raw.len()
        );
        stream.write_all(head.as_bytes()).unwrap();
        stream.write_all(raw.as_bytes()).unwrap();
        stream.flush().unwrap();
        let mut answer = Vec::new();
        stream.read_to_end(&mut answer).unwrap();
        let text = String::from_utf8_lossy(&answer);
        let (head, payload) = text.split_once("\r\n\r\n").expect("a header and a body");
        let status: u16 = head.split_whitespace().nth(1).unwrap().parse().unwrap();
        (status, json::parse(payload).expect("a JSON body"))
    }
}

fn object(members: Vec<(&str, &str)>) -> Value {
    Value::object(
        members
            .into_iter()
            .map(|(k, v)| (k, Value::string(v)))
            .collect(),
    )
}

#[test]
fn a_creation_is_201_and_a_read_is_200() {
    let api = Api::new("2026-10-01");
    let (status, _) = api.call(
        "POST",
        "/titles",
        Some(&object(vec![
            ("isbn", "1"),
            ("title", "T"),
            ("author", "A"),
        ])),
    );
    assert_eq!(status, 201);
    api.call(
        "POST",
        "/copies",
        Some(&object(vec![("copy", "c"), ("isbn", "1")])),
    );
    let (status, body) = api.call("GET", "/copies/c", None);
    assert_eq!(
        (status, body.get("status").and_then(Value::as_str)),
        (200, Some("available"))
    );
}

#[test]
fn unknown_is_404_and_a_rule_is_409() {
    let api = Api::new("2026-10-01");
    let (status, body) = api.call("GET", "/copies/nope", None);
    assert_eq!(
        (status, body.get("error").and_then(Value::as_str)),
        (404, Some("unknown-copy"))
    );
    let title = object(vec![("isbn", "1"), ("title", "T"), ("author", "A")]);
    api.call("POST", "/titles", Some(&title));
    let (status, body) = api.call("POST", "/titles", Some(&title));
    assert_eq!(
        (status, body.get("error").and_then(Value::as_str)),
        (409, Some("duplicate-title"))
    );
}

#[test]
fn a_malformed_request_is_400_and_names_what_is_wrong() {
    let api = Api::new("2026-10-01");
    let (status, body) = api.call_raw("POST", "/titles", "{not json");
    assert_eq!(
        (status, body.get("error").and_then(Value::as_str)),
        (400, Some("bad-request"))
    );
    let (status, body) = api.call("POST", "/titles", Some(&object(vec![("isbn", "1")])));
    assert_eq!(status, 400);
    let message = body.get("message").and_then(Value::as_str).unwrap_or("");
    assert!(message.contains("title, author"), "{message}");
    let (status, body) = api.call(
        "POST",
        "/members",
        Some(&object(vec![("member", "m"), ("name", "N"), ("age", "9")])),
    );
    assert_eq!(status, 400);
    let message = body.get("message").and_then(Value::as_str).unwrap_or("");
    assert!(message.contains("takes no age"), "{message}");
}

#[test]
fn an_unknown_route_is_404() {
    let api = Api::new("2026-10-01");
    let (status, body) = api.call("POST", "/loans/overdue", None);
    assert_eq!(
        (status, body.get("error").and_then(Value::as_str)),
        (404, Some("no-route"))
    );
    let (status, body) = api.call("GET", "/shelves", None);
    assert_eq!(
        (status, body.get("error").and_then(Value::as_str)),
        (404, Some("no-route"))
    );
}

#[test]
fn the_date_is_the_servers_clock_not_the_clients() {
    let api = Api::new("2026-10-01");
    api.call(
        "POST",
        "/titles",
        Some(&object(vec![
            ("isbn", "1"),
            ("title", "T"),
            ("author", "A"),
        ])),
    );
    api.call(
        "POST",
        "/copies",
        Some(&object(vec![("copy", "c"), ("isbn", "1")])),
    );
    api.call(
        "POST",
        "/members",
        Some(&object(vec![("member", "m"), ("name", "N")])),
    );
    api.clock.set(Date::from_ymd(2026, 12, 25));
    let (status, body) = api.call(
        "POST",
        "/loans",
        Some(&object(vec![("member", "m"), ("copy", "c")])),
    );
    assert_eq!(
        (
            status,
            body.get("lent").and_then(Value::as_str),
            body.get("due").and_then(Value::as_str)
        ),
        (201, Some("2026-12-25"), Some("2027-01-15"))
    );
}

#[test]
fn the_routes_agree_with_the_vector_forms() {
    // A guard on the test support itself: every operation a vector names has an HTTP form,
    // with the method that form implies.
    let vectors = common::vectors();
    let scenarios = vectors
        .get("scenarios")
        .and_then(Value::as_array)
        .expect("scenarios");
    let mut seen: Vec<String> = Vec::new();
    for scenario in scenarios {
        for step in common::steps_of(&vectors, scenario) {
            let op = step
                .get("op")
                .and_then(Value::as_str)
                .expect("an op")
                .to_string();
            if seen.contains(&op) {
                continue;
            }
            let args = step
                .get("args")
                .cloned()
                .unwrap_or(Value::Object(Default::default()));
            let (method, path, body) = http_request(&op, &args);
            let reads = matches!(op.as_str(), "copy" | "loans" | "overdue");
            // A body exactly when the operation's arguments come from it, not from the path.
            let writes_a_body = matches!(
                op.as_str(),
                "add_title" | "add_copy" | "add_member" | "lend"
            );
            assert_eq!(method, if reads { "GET" } else { "POST" }, "{op}");
            assert_eq!(
                body.is_some(),
                writes_a_body,
                "{op}: a body exactly when it writes one"
            );
            assert!(path.starts_with('/'), "{op}: {path}");
            seen.push(op);
        }
    }
    assert_eq!(seen.len(), 9, "every operation was seen: {seen:?}");
}
