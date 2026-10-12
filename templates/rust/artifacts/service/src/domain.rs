//! The library's rules: catalog, members, loans, renewals and overdue loans.
//!
//! Time is an INPUT. Every operation that depends on the date takes `today` as an argument,
//! and nothing in this module reads a clock, so a test, a scenario vector and a book chapter
//! all get the same answer on any day they run. `date::Clock` is the one thing that holds a
//! date, and the HTTP layer is handed the clock it asks.
//!
//! Every answer is a `json::Value` object, the same shape the HTTP API serves and the
//! scenario vectors (`openspec/vectors.json`) expect.

use std::collections::BTreeMap;

use crate::date::Date;
use crate::json::Value;

pub const LOAN_DAYS: i64 = 21;
pub const MAX_LOANS: usize = 3;
pub const MAX_RENEWALS: u32 = 2;
pub const FEE_CENTS_PER_DAY: i64 = 25;

/// An operation the rules refuse. `code` is the stable name the specs and vectors use.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refused {
    pub code: String,
    pub message: String,
}

impl Refused {
    pub fn new(code: &str, message: String) -> Refused {
        Refused {
            code: code.to_string(),
            message,
        }
    }
}

/// What every operation answers: its result, or the refusal that says why not.
pub type Answer = Result<Value, Refused>;

#[derive(Debug, Clone)]
pub struct Title {
    pub isbn: String,
    pub title: String,
    pub author: String,
}

#[derive(Debug, Clone)]
pub struct Copy {
    pub copy: String,
    pub isbn: String,
}

#[derive(Debug, Clone)]
pub struct Member {
    pub member: String,
    pub name: String,
}

#[derive(Debug, Clone)]
pub struct Loan {
    pub copy: String,
    pub member: String,
    pub lent: Date,
    pub due: Date,
    pub renewals: u32,
}

impl Loan {
    /// A loan is overdue from the day AFTER its due date.
    pub fn days_late(&self, today: Date) -> i64 {
        today.days_since(self.due).max(0)
    }

    fn answer(&self) -> Value {
        Value::object(vec![
            ("copy", Value::string(&self.copy)),
            ("member", Value::string(&self.member)),
            ("lent", Value::string(&self.lent.to_iso())),
            ("due", Value::string(&self.due.to_iso())),
            ("renewals", Value::integer(self.renewals as i64)),
        ])
    }
}

/// One library's state, in memory.
#[derive(Debug, Default)]
pub struct Library {
    pub titles: BTreeMap<String, Title>,
    pub copies: BTreeMap<String, Copy>,
    pub members: BTreeMap<String, Member>,
    /// by copy: a copy is out to one member at a time
    pub loans: BTreeMap<String, Loan>,
}

impl Library {
    pub fn new() -> Library {
        Library::default()
    }

    // ------------------------------------------------------------- catalog and members

    pub fn add_title(&mut self, isbn: &str, title: &str, author: &str) -> Answer {
        if self.titles.contains_key(isbn) {
            return Err(Refused::new(
                "duplicate-title",
                format!("ISBN {isbn} is already cataloged"),
            ));
        }
        self.titles.insert(
            isbn.to_string(),
            Title {
                isbn: isbn.to_string(),
                title: title.to_string(),
                author: author.to_string(),
            },
        );
        Ok(Value::object(vec![
            ("isbn", Value::string(isbn)),
            ("title", Value::string(title)),
            ("author", Value::string(author)),
        ]))
    }

    pub fn add_copy(&mut self, copy: &str, isbn: &str) -> Answer {
        if !self.titles.contains_key(isbn) {
            return Err(Refused::new(
                "unknown-title",
                format!("no title with ISBN {isbn} is cataloged"),
            ));
        }
        if self.copies.contains_key(copy) {
            return Err(Refused::new(
                "duplicate-copy",
                format!("copy {copy} already exists"),
            ));
        }
        self.copies.insert(
            copy.to_string(),
            Copy {
                copy: copy.to_string(),
                isbn: isbn.to_string(),
            },
        );
        self.copy_status(copy)
    }

    pub fn add_member(&mut self, member: &str, name: &str) -> Answer {
        if self.members.contains_key(member) {
            return Err(Refused::new(
                "duplicate-member",
                format!("member {member} is already registered"),
            ));
        }
        self.members.insert(
            member.to_string(),
            Member {
                member: member.to_string(),
                name: name.to_string(),
            },
        );
        Ok(Value::object(vec![
            ("member", Value::string(member)),
            ("name", Value::string(name)),
        ]))
    }

    pub fn copy_status(&self, copy: &str) -> Answer {
        let found = self.copy_of(copy)?;
        let loan = self.loans.get(copy);
        Ok(Value::object(vec![
            ("copy", Value::string(copy)),
            ("isbn", Value::string(&found.isbn)),
            (
                "status",
                Value::string(if loan.is_some() {
                    "on-loan"
                } else {
                    "available"
                }),
            ),
            (
                "loan",
                loan.map(|loan| loan.answer()).unwrap_or(Value::Null),
            ),
        ]))
    }

    // ---------------------------------------------------------------------------- loans

    pub fn lend(&mut self, member: &str, copy: &str, today: Date) -> Answer {
        self.member_of(member)?;
        self.copy_of(copy)?;
        if let Some(out) = self.loans.get(copy) {
            return Err(Refused::new(
                "copy-on-loan",
                format!("copy {copy} is out until {}", out.due.to_iso()),
            ));
        }
        let theirs: Vec<&Loan> = self
            .loans
            .values()
            .filter(|loan| loan.member == member)
            .collect();
        if theirs.iter().any(|loan| loan.days_late(today) > 0) {
            return Err(Refused::new(
                "member-has-overdue",
                format!("member {member} has an overdue loan"),
            ));
        }
        if theirs.len() >= MAX_LOANS {
            return Err(Refused::new(
                "loan-limit",
                format!("member {member} already has {MAX_LOANS} loans"),
            ));
        }
        let loan = Loan {
            copy: copy.to_string(),
            member: member.to_string(),
            lent: today,
            due: today.plus_days(LOAN_DAYS),
            renewals: 0,
        };
        let answer = loan.answer();
        self.loans.insert(copy.to_string(), loan);
        Ok(answer)
    }

    pub fn renew(&mut self, copy: &str, today: Date) -> Answer {
        let loan = self.loan_of(copy)?;
        if loan.days_late(today) > 0 {
            return Err(Refused::new(
                "loan-overdue",
                format!(
                    "copy {copy} was due {}; return it instead",
                    loan.due.to_iso()
                ),
            ));
        }
        if loan.renewals >= MAX_RENEWALS {
            return Err(Refused::new(
                "renewal-limit",
                format!("copy {copy} has been renewed {MAX_RENEWALS} times"),
            ));
        }
        let loan = self.loans.get_mut(copy).expect("loan_of found it");
        loan.due = loan.due.plus_days(LOAN_DAYS);
        loan.renewals += 1;
        Ok(loan.answer())
    }

    pub fn give_back(&mut self, copy: &str, today: Date) -> Answer {
        let loan = self.loan_of(copy)?;
        let late = loan.days_late(today);
        let answer = Value::object(vec![
            ("copy", Value::string(copy)),
            ("member", Value::string(&loan.member)),
            ("returned", Value::string(&today.to_iso())),
            ("days_late", Value::integer(late)),
            ("fee_cents", Value::integer(late * FEE_CENTS_PER_DAY)),
        ]);
        self.loans.remove(copy);
        Ok(answer)
    }

    pub fn loans_of(&self, member: &str, today: Date) -> Answer {
        self.member_of(member)?;
        let mut theirs: Vec<&Loan> = self
            .loans
            .values()
            .filter(|loan| loan.member == member)
            .collect();
        theirs.sort_by(|a, b| a.due.cmp(&b.due).then_with(|| a.copy.cmp(&b.copy)));
        let mut rows = Vec::new();
        for loan in theirs {
            let isbn = self
                .copies
                .get(&loan.copy)
                .expect("a loan's copy exists")
                .isbn
                .clone();
            let title = self.titles.get(&isbn).expect("a copy's title exists");
            let late = loan.days_late(today);
            rows.push(Value::object(vec![
                ("copy", Value::string(&loan.copy)),
                ("isbn", Value::string(&title.isbn)),
                ("title", Value::string(&title.title)),
                ("due", Value::string(&loan.due.to_iso())),
                ("renewals", Value::integer(loan.renewals as i64)),
                ("overdue", Value::boolean(late > 0)),
                ("days_late", Value::integer(late)),
            ]));
        }
        Ok(Value::object(vec![
            ("member", Value::string(member)),
            ("loans", Value::Array(rows)),
        ]))
    }

    pub fn overdue(&self, today: Date) -> Answer {
        let mut late: Vec<&Loan> = self
            .loans
            .values()
            .filter(|loan| loan.days_late(today) > 0)
            .collect();
        late.sort_by(|a, b| {
            b.days_late(today)
                .cmp(&a.days_late(today))
                .then_with(|| a.copy.cmp(&b.copy))
        });
        let rows = late
            .into_iter()
            .map(|loan| {
                Value::object(vec![
                    ("copy", Value::string(&loan.copy)),
                    ("member", Value::string(&loan.member)),
                    ("due", Value::string(&loan.due.to_iso())),
                    ("days_late", Value::integer(loan.days_late(today))),
                ])
            })
            .collect();
        Ok(Value::object(vec![("overdue", Value::Array(rows))]))
    }

    // ------------------------------------------------------------------------- lookups

    fn member_of(&self, member: &str) -> Result<&Member, Refused> {
        self.members
            .get(member)
            .ok_or_else(|| Refused::new("unknown-member", format!("no member {member}")))
    }

    fn copy_of(&self, copy: &str) -> Result<&Copy, Refused> {
        self.copies
            .get(copy)
            .ok_or_else(|| Refused::new("unknown-copy", format!("no copy {copy}")))
    }

    fn loan_of(&self, copy: &str) -> Result<&Loan, Refused> {
        self.copy_of(copy)?;
        self.loans
            .get(copy)
            .ok_or_else(|| Refused::new("not-on-loan", format!("copy {copy} is not on loan")))
    }
}

/// The named arguments of one operation: what is left over when the operation has asked for
/// all of them is an argument it does not take, and a name it asks for that is absent (or
/// not a non-empty string) is a missing argument. Both are refused, naming every one.
struct Args {
    op: String,
    members: BTreeMap<String, Value>,
}

impl Args {
    fn new(op: &str, value: &Value) -> Result<Args, Refused> {
        match value.as_object() {
            Some(members) => Ok(Args {
                op: op.to_string(),
                members: members.clone(),
            }),
            None => Err(Refused::new(
                "bad-request",
                "the body is a JSON object".to_string(),
            )),
        }
    }

    /// The values of `names`, in order, once nothing else is present and none is missing.
    fn take(&self, names: &[&str]) -> Result<Vec<String>, Refused> {
        let extra: Vec<&str> = self
            .members
            .keys()
            .map(String::as_str)
            .filter(|name| !names.contains(name))
            .collect();
        if !extra.is_empty() {
            return Err(Refused::new(
                "bad-request",
                format!("{} takes no {}", self.op, extra.join(", ")),
            ));
        }
        let missing: Vec<&str> = names
            .iter()
            .copied()
            .filter(|name| {
                !matches!(self.members.get(*name).and_then(Value::as_str), Some(text) if !text.is_empty())
            })
            .collect();
        if !missing.is_empty() {
            return Err(Refused::new(
                "bad-request",
                format!(
                    "{} needs {} (non-empty strings)",
                    self.op,
                    missing.join(", ")
                ),
            ));
        }
        Ok(names
            .iter()
            .map(|name| self.members[*name].as_str().unwrap_or_default().to_string())
            .collect())
    }
}

/// Run one named operation: the routing table the HTTP layer and a vector-driven test both
/// come through. Refuses an unknown operation, a missing argument and an argument the
/// operation does not take, rather than guessing.
pub fn apply(library: &mut Library, op: &str, args: &Value, today: Date) -> Answer {
    let args = Args::new(op, args)?;
    match op {
        "add_title" => {
            let values = args.take(&["isbn", "title", "author"])?;
            library.add_title(&values[0], &values[1], &values[2])
        }
        "add_copy" => {
            let values = args.take(&["copy", "isbn"])?;
            library.add_copy(&values[0], &values[1])
        }
        "add_member" => {
            let values = args.take(&["member", "name"])?;
            library.add_member(&values[0], &values[1])
        }
        "copy" => {
            let values = args.take(&["copy"])?;
            library.copy_status(&values[0])
        }
        "lend" => {
            let values = args.take(&["member", "copy"])?;
            library.lend(&values[0], &values[1], today)
        }
        "renew" => {
            let values = args.take(&["copy"])?;
            library.renew(&values[0], today)
        }
        "return" => {
            let values = args.take(&["copy"])?;
            library.give_back(&values[0], today)
        }
        "loans" => {
            let values = args.take(&["member"])?;
            library.loans_of(&values[0], today)
        }
        "overdue" => {
            args.take(&[])?;
            library.overdue(today)
        }
        _ => Err(Refused::new("bad-request", format!("no operation {op:?}"))),
    }
}
