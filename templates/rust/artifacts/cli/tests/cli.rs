//! The command line against the real service, on a free port with a clock the test sets.

use std::sync::Arc;

use lending_cli::{parse, run, Client, Command};
use lending_service::date::{Clock, Date};
use lending_service::domain::Library;
use lending_service::http::Server;

struct Cli {
    service: String,
    clock: Arc<Clock>,
    _server: Server,
}

impl Cli {
    fn new(today: &str) -> Cli {
        let clock = Arc::new(Clock::fixed(Date::parse(today).expect("a date")));
        let server = Server::start(Library::new(), Arc::clone(&clock), "127.0.0.1", 0, false)
            .expect("a free port");
        Cli {
            service: server.base(),
            clock,
            _server: server,
        }
    }

    /// The command line, as `library --service <url> …` would be parsed, and its run.
    fn cli(&self, argv: &[&str]) -> (u8, String, String) {
        let mut full: Vec<String> = vec!["--service".to_string(), self.service.clone()];
        full.extend(argv.iter().map(|word| word.to_string()));
        let command: Command = parse(&full).expect("a command line");
        let client = Client::new(&command.service);
        let mut out = Vec::new();
        let mut err = Vec::new();
        let status = run(&command, &client, &mut out, &mut err);
        (
            status,
            String::from_utf8_lossy(&out).trim_end().to_string(),
            String::from_utf8_lossy(&err).trim_end().to_string(),
        )
    }

    fn shelf(&self) {
        for argv in [
            vec![
                "add-title",
                "9780141439518",
                "Pride and Prejudice",
                "Jane Austen",
            ],
            vec!["add-copy", "pp-1", "9780141439518"],
            vec!["add-copy", "pp-2", "9780141439518"],
            vec!["add-member", "ada", "Ada Lovelace"],
            vec!["add-member", "grace", "Grace Hopper"],
        ] {
            let (status, _, err) = self.cli(&argv);
            assert_eq!(status, 0, "{err}");
        }
    }
}

#[test]
fn cataloging_answers_in_words() {
    let cli = Cli::new("2026-10-01");
    assert_eq!(
        cli.cli(&[
            "add-title",
            "9780141439518",
            "Pride and Prejudice",
            "Jane Austen"
        ]),
        (
            0,
            "cataloged 9780141439518: Pride and Prejudice by Jane Austen".to_string(),
            String::new()
        )
    );
}

#[test]
fn a_loan_from_lending_to_a_late_return() {
    let cli = Cli::new("2026-10-01");
    cli.shelf();
    assert_eq!(
        cli.cli(&["lend", "ada", "pp-1"]).1,
        "lent pp-1 to ada, due 2026-10-22"
    );
    assert_eq!(
        cli.cli(&["copy", "pp-1"]).1,
        "pp-1 (9780141439518): on loan, pp-1 to ada, due 2026-10-22"
    );
    assert_eq!(
        cli.cli(&["renew", "pp-1"]).1,
        "renewed pp-1 to ada, due 2026-11-12, renewed 1x"
    );
    cli.clock.set(Date::from_ymd(2026, 11, 14));
    let (status, out, _) = cli.cli(&["loans", "ada"]);
    assert_eq!(status, 0);
    assert_eq!(
        out,
        "pp-1     due 2026-11-12  OVERDUE 2 day(s)  Pride and Prejudice"
    );
    assert_eq!(
        cli.cli(&["overdue"]).1,
        "pp-1     ada        due 2026-11-12  2 day(s) late"
    );
    assert_eq!(
        cli.cli(&["return", "pp-1"]).1,
        "returned pp-1 from ada, 2 day(s) late: owes $0.50"
    );
    assert_eq!(cli.cli(&["overdue"]).1, "nothing is overdue");
    assert_eq!(cli.cli(&["loans", "ada"]).1, "ada has no loans");
}

#[test]
fn a_refusal_is_exit_1_with_the_services_code() {
    let cli = Cli::new("2026-10-01");
    cli.shelf();
    cli.cli(&["lend", "ada", "pp-1"]);
    let (status, out, err) = cli.cli(&["lend", "grace", "pp-1"]);
    assert_eq!((status, out.as_str()), (1, ""));
    assert!(
        err.starts_with("library: refused (copy-on-loan): copy pp-1 is out"),
        "{err}"
    );
}

#[test]
fn no_service_is_exit_2() {
    // A port nothing listens on: bound and then released, so nothing can be racing for it.
    let port = {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("a free port");
        listener.local_addr().expect("an address").port()
    };
    let argv: Vec<String> = vec![
        "--service".to_string(),
        format!("http://127.0.0.1:{port}"),
        "overdue".to_string(),
    ];
    let command = parse(&argv).expect("a command line");
    let mut out = Vec::new();
    let mut err = Vec::new();
    let status = run(&command, &Client::new(&command.service), &mut out, &mut err);
    assert_eq!(status, 2);
    let err = String::from_utf8_lossy(&err);
    assert!(err.contains("no answer from"), "{err}");
}
