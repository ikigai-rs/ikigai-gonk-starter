//! Shared test support: the scenario vectors, and the HTTP form of each operation.

use std::path::{Path, PathBuf};

use lending_service::json::{self, Value};

/// The repository root: the crate is artifacts/service, and the vectors are at openspec/.
pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .canonicalize()
        .expect("a root")
}

/// `openspec/vectors.json`, the language-neutral half of the spec.
pub fn vectors() -> Value {
    let text = std::fs::read_to_string(repo_root().join("openspec").join("vectors.json"))
        .expect("openspec/vectors.json");
    json::parse(&text).expect("the vectors are JSON")
}

/// The fixtures a scenario names, then its own steps.
pub fn steps_of(vectors: &Value, scenario: &Value) -> Vec<Value> {
    let fixtures = vectors.get("fixtures");
    let mut steps = Vec::new();
    if let Some(names) = scenario.get("given").and_then(Value::as_array) {
        for name in names {
            let name = name.as_str().expect("a fixture name");
            let fixture = fixtures
                .and_then(|all| all.get(name))
                .and_then(Value::as_array)
                .unwrap_or_else(|| panic!("no fixture {name}"));
            steps.extend(fixture.iter().cloned());
        }
    }
    if let Some(own) = scenario.get("steps").and_then(Value::as_array) {
        steps.extend(own.iter().cloned());
    }
    steps
}

/// How each operation is asked over HTTP: its method, its path, and the body if any.
pub fn http_request(op: &str, args: &Value) -> (&'static str, String, Option<Value>) {
    let text = |key: &str| {
        args.get(key)
            .and_then(Value::as_str)
            .unwrap_or_else(|| panic!("{op} needs {key}"))
            .to_string()
    };
    match op {
        "add_title" => ("POST", "/titles".to_string(), Some(args.clone())),
        "add_copy" => ("POST", "/copies".to_string(), Some(args.clone())),
        "add_member" => ("POST", "/members".to_string(), Some(args.clone())),
        "lend" => ("POST", "/loans".to_string(), Some(args.clone())),
        "copy" => ("GET", format!("/copies/{}", text("copy")), None),
        "renew" => ("POST", format!("/loans/{}/renewal", text("copy")), None),
        "return" => ("POST", format!("/loans/{}/return", text("copy")), None),
        "loans" => ("GET", format!("/members/{}/loans", text("member")), None),
        "overdue" => ("GET", "/loans/overdue".to_string(), None),
        other => panic!("no HTTP form for {other}"),
    }
}
