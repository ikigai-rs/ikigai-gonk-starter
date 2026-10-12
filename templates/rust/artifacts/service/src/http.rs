//! The library's HTTP JSON API: a routing table over `domain`, and nothing else.
//!
//! ```text
//! POST /titles                 {"isbn", "title", "author"}   catalog a title
//! POST /copies                 {"copy", "isbn"}              add a copy of it
//! POST /members                {"member", "name"}            register a member
//! GET  /copies/{copy}                                        a copy, and its loan if out
//! POST /loans                  {"member", "copy"}            lend a copy
//! POST /loans/{copy}/renewal                                 renew its loan
//! POST /loans/{copy}/return                                  return it
//! GET  /members/{member}/loans                               a member's loans
//! GET  /loans/overdue                                        every overdue loan
//! ```
//!
//! A refusal is `{"error": <code>, "message": <text>}` with 404 for something unknown, 400
//! for a malformed request and 409 for a rule. The DATE of every request is the server's
//! clock, never the client's: the clock is an argument of `Server::start`, so a test hands it
//! a fixed or settable one and `serve --today` pins it for a demonstration.
//!
//! The wire is HTTP/1.1 by hand, on the standard library's TCP: one request per connection,
//! `Connection: close`, a `Content-Length` and a JSON body. That is the whole of it, and it
//! is why this artifact has no dependencies.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};

use crate::date::Clock;
use crate::domain::{apply, Library, Refused};
use crate::json::{self, Value};

pub const MAX_BODY: usize = 64 * 1024;
const MAX_LINE: u64 = 8 * 1024;

/// One route: its method, the path pattern, the operation, and whether the operation's
/// arguments come from the body.
struct Route {
    method: &'static str,
    pattern: &'static str,
    op: &'static str,
    from_body: bool,
}

const ROUTES: &[Route] = &[
    Route {
        method: "POST",
        pattern: "/titles",
        op: "add_title",
        from_body: true,
    },
    Route {
        method: "POST",
        pattern: "/copies",
        op: "add_copy",
        from_body: true,
    },
    Route {
        method: "POST",
        pattern: "/members",
        op: "add_member",
        from_body: true,
    },
    Route {
        method: "GET",
        pattern: "/copies/{copy}",
        op: "copy",
        from_body: false,
    },
    Route {
        method: "POST",
        pattern: "/loans",
        op: "lend",
        from_body: true,
    },
    Route {
        method: "GET",
        pattern: "/loans/overdue",
        op: "overdue",
        from_body: false,
    },
    Route {
        method: "POST",
        pattern: "/loans/{copy}/renewal",
        op: "renew",
        from_body: false,
    },
    Route {
        method: "POST",
        pattern: "/loans/{copy}/return",
        op: "return",
        from_body: false,
    },
    Route {
        method: "GET",
        pattern: "/members/{member}/loans",
        op: "loans",
        from_body: false,
    },
];

fn creates(op: &str) -> bool {
    matches!(op, "add_title" | "add_copy" | "add_member" | "lend")
}

fn status_for(code: &str) -> u16 {
    match code {
        "unknown-title" | "unknown-copy" | "unknown-member" | "no-route" => 404,
        "bad-request" => 400,
        _ => 409,
    }
}

fn reason_for(status: u16) -> &'static str {
    match status {
        200 => "OK",
        201 => "Created",
        400 => "Bad Request",
        404 => "Not Found",
        _ => "Conflict",
    }
}

/// The path a pattern matches, and what its `{name}` segments took: the whole path, segment
/// by segment, so `/copies/{copy}` takes one segment and nothing across a `/`.
fn match_path(pattern: &str, path: &str) -> Option<Vec<(String, String)>> {
    let want: Vec<&str> = pattern.split('/').collect();
    let have: Vec<&str> = path.split('/').collect();
    if want.len() != have.len() {
        return None;
    }
    let mut captured = Vec::new();
    for (wanted, found) in want.iter().zip(&have) {
        match wanted
            .strip_prefix('{')
            .and_then(|rest| rest.strip_suffix('}'))
        {
            Some(name) if !found.is_empty() => captured.push((name.to_string(), found.to_string())),
            Some(_) => return None,
            None if wanted == found => {}
            None => return None,
        }
    }
    Some(captured)
}

struct Shared {
    library: Mutex<Library>,
    clock: Arc<Clock>,
    verbose: bool,
}

/// A server on a port, running until it is dropped. `Server::start` answers as soon as the
/// port is bound, so a test can send a request at once.
pub struct Server {
    address: SocketAddr,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Server {
    /// Start a server for `library`, dating every request with `clock`. Port 0 picks a free
    /// one.
    pub fn start(
        library: Library,
        clock: Arc<Clock>,
        host: &str,
        port: u16,
        verbose: bool,
    ) -> std::io::Result<Server> {
        let listener = TcpListener::bind((host, port))?;
        let address = listener.local_addr()?;
        let stop = Arc::new(AtomicBool::new(false));
        let shared = Arc::new(Shared {
            library: Mutex::new(library),
            clock,
            verbose,
        });
        let flag = Arc::clone(&stop);
        let thread = thread::spawn(move || {
            for incoming in listener.incoming() {
                if flag.load(Ordering::SeqCst) {
                    break;
                }
                if let Ok(stream) = incoming {
                    let shared = Arc::clone(&shared);
                    thread::spawn(move || {
                        let _ = answer(stream, &shared);
                    });
                }
            }
        });
        Ok(Server {
            address,
            stop,
            thread: Some(thread),
        })
    }

    pub fn address(&self) -> SocketAddr {
        self.address
    }

    /// The URL the service is at, `http://host:port`.
    pub fn base(&self) -> String {
        format!("http://{}:{}", self.address.ip(), self.address.port())
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        // The accept loop has no timeout, so wake it: a connection it accepts and then sees
        // the flag on is how it ends.
        let _ = TcpStream::connect(self.address);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// One request, one answer, then the connection closes.
fn answer(mut stream: TcpStream, shared: &Shared) -> std::io::Result<()> {
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut request_line = String::new();
    if reader
        .by_ref()
        .take(MAX_LINE)
        .read_line(&mut request_line)?
        == 0
    {
        return Ok(()); // a connection that said nothing
    }
    let mut parts = request_line.trim_end().split(' ');
    let (method, path) = (parts.next().unwrap_or(""), parts.next().unwrap_or("/"));
    let path = path.split('?').next().unwrap_or(path);

    let mut length = 0usize;
    let mut header = String::new();
    loop {
        header.clear();
        if reader.by_ref().take(MAX_LINE).read_line(&mut header)? == 0
            || header.trim_end().is_empty()
        {
            break;
        }
        if let Some((name, value)) = header.split_once(':') {
            if name.eq_ignore_ascii_case("content-length") {
                length = value.trim().parse().unwrap_or(0);
            }
        }
    }
    if length > MAX_BODY {
        return reply(
            &mut stream,
            400,
            &refusal(
                "bad-request",
                &format!("a body is at most {MAX_BODY} bytes"),
            ),
        );
    }
    let mut body = vec![0u8; length];
    reader.read_exact(&mut body)?;

    let (status, value) = dispatch(method, path, &body, shared);
    if shared.verbose {
        eprintln!("{method} {path} -> {status}");
    }
    reply(&mut stream, status, &value)
}

fn dispatch(method: &str, path: &str, body: &[u8], shared: &Shared) -> (u16, Value) {
    for route in ROUTES {
        if route.method != method {
            continue;
        }
        let Some(captured) = match_path(route.pattern, path) else {
            continue;
        };
        let args = if route.from_body {
            let text = if body.is_empty() {
                "{}".to_string()
            } else {
                String::from_utf8_lossy(body).into_owned()
            };
            match json::parse(&text) {
                Ok(value @ Value::Object(_)) => value,
                Ok(_) => return (400, refusal("bad-request", "the body is a JSON object")),
                Err(e) => {
                    return (
                        400,
                        refusal("bad-request", &format!("the body is not JSON: {e}")),
                    )
                }
            }
        } else {
            Value::Object(
                captured
                    .into_iter()
                    .map(|(name, found)| (name, Value::string(&found)))
                    .collect(),
            )
        };
        let today = shared.clock.today();
        let mut library = shared
            .library
            .lock()
            .expect("no request panicked while holding the lock");
        return match apply(&mut library, route.op, &args, today) {
            Ok(answer) => (if creates(route.op) { 201 } else { 200 }, answer),
            Err(refused) => (
                status_for(&refused.code),
                refusal(&refused.code, &refused.message),
            ),
        };
    }
    (
        404,
        refusal("no-route", &format!("no route {method} {path}")),
    )
}

fn refusal(code: &str, message: &str) -> Value {
    Value::object(vec![
        ("error", Value::string(code)),
        ("message", Value::string(message)),
    ])
}

fn reply(stream: &mut TcpStream, status: u16, value: &Value) -> std::io::Result<()> {
    let body = json::to_text(value);
    let head = format!(
        "HTTP/1.1 {status} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        reason_for(status),
        body.len()
    );
    stream.write_all(head.as_bytes())?;
    stream.write_all(body.as_bytes())?;
    stream.flush()
}

/// A refusal from the domain, for a caller that wants the pair.
impl From<Refused> for (u16, Value) {
    fn from(refused: Refused) -> (u16, Value) {
        (
            status_for(&refused.code),
            refusal(&refused.code, &refused.message),
        )
    }
}
