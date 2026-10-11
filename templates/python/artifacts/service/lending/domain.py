"""The library's rules: catalog, members, loans, renewals and overdue loans.

Time is an INPUT. Every operation that depends on the date takes `today` as an argument, and
nothing in this module reads a clock, so a test, a scenario vector and a book chapter all
get the same answer on any day they run. The HTTP layer (`lending.http`) is the one place
that asks a clock, and it is handed the clock it asks.

Every answer is a plain dict of strings, integers and booleans, the same shape the HTTP API
serves as JSON and the scenario vectors (`openspec/vectors.json`) expect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

LOAN_DAYS = 21
MAX_LOANS = 3
MAX_RENEWALS = 2
FEE_CENTS_PER_DAY = 25


class Refused(Exception):
    """An operation the rules refuse. `code` is the stable name the specs and vectors use."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Title:
    isbn: str
    title: str
    author: str


@dataclass
class Copy:
    copy: str
    isbn: str


@dataclass
class Member:
    member: str
    name: str


@dataclass
class Loan:
    copy: str
    member: str
    lent: date
    due: date
    renewals: int = 0

    def days_late(self, today: date) -> int:
        """A loan is overdue from the day AFTER its due date."""
        return max(0, (today - self.due).days)


class Library:
    """One library's state, in memory."""

    def __init__(self) -> None:
        self.titles: dict[str, Title] = {}
        self.copies: dict[str, Copy] = {}
        self.members: dict[str, Member] = {}
        self.loans: dict[str, Loan] = {}  # by copy: a copy is out to one member at a time

    # ----------------------------------------------------------------- catalog and members

    def add_title(self, isbn: str, title: str, author: str) -> dict:
        if isbn in self.titles:
            raise Refused("duplicate-title", f"ISBN {isbn} is already cataloged")
        self.titles[isbn] = Title(isbn, title, author)
        return {"isbn": isbn, "title": title, "author": author}

    def add_copy(self, copy: str, isbn: str) -> dict:
        if isbn not in self.titles:
            raise Refused("unknown-title", f"no title with ISBN {isbn} is cataloged")
        if copy in self.copies:
            raise Refused("duplicate-copy", f"copy {copy} already exists")
        self.copies[copy] = Copy(copy, isbn)
        return self.copy_status(copy)

    def add_member(self, member: str, name: str) -> dict:
        if member in self.members:
            raise Refused("duplicate-member", f"member {member} is already registered")
        self.members[member] = Member(member, name)
        return {"member": member, "name": name}

    def copy_status(self, copy: str) -> dict:
        found = self._copy(copy)
        loan = self.loans.get(copy)
        return {
            "copy": copy,
            "isbn": found.isbn,
            "status": "on-loan" if loan else "available",
            "loan": self._loan(loan) if loan else None,
        }

    # ----------------------------------------------------------------- loans

    def lend(self, member: str, copy: str, today: date) -> dict:
        self._member(member)
        self._copy(copy)
        if copy in self.loans:
            raise Refused("copy-on-loan", f"copy {copy} is out until {self.loans[copy].due}")
        theirs = [loan for loan in self.loans.values() if loan.member == member]
        if any(loan.days_late(today) > 0 for loan in theirs):
            raise Refused("member-has-overdue", f"member {member} has an overdue loan")
        if len(theirs) >= MAX_LOANS:
            raise Refused("loan-limit", f"member {member} already has {MAX_LOANS} loans")
        loan = Loan(copy, member, today, today + timedelta(days=LOAN_DAYS))
        self.loans[copy] = loan
        return self._loan(loan)

    def renew(self, copy: str, today: date) -> dict:
        loan = self._out(copy)
        if loan.days_late(today) > 0:
            raise Refused("loan-overdue", f"copy {copy} was due {loan.due}; return it instead")
        if loan.renewals >= MAX_RENEWALS:
            raise Refused("renewal-limit", f"copy {copy} has been renewed {MAX_RENEWALS} times")
        loan.due += timedelta(days=LOAN_DAYS)
        loan.renewals += 1
        return self._loan(loan)

    def give_back(self, copy: str, today: date) -> dict:
        loan = self._out(copy)
        del self.loans[copy]
        late = loan.days_late(today)
        return {
            "copy": copy,
            "member": loan.member,
            "returned": today.isoformat(),
            "days_late": late,
            "fee_cents": late * FEE_CENTS_PER_DAY,
        }

    def loans_of(self, member: str, today: date) -> dict:
        self._member(member)
        theirs = sorted(
            (loan for loan in self.loans.values() if loan.member == member),
            key=lambda loan: (loan.due, loan.copy),
        )
        rows = []
        for loan in theirs:
            title = self.titles[self.copies[loan.copy].isbn]
            late = loan.days_late(today)
            rows.append(
                {
                    "copy": loan.copy,
                    "isbn": title.isbn,
                    "title": title.title,
                    "due": loan.due.isoformat(),
                    "renewals": loan.renewals,
                    "overdue": late > 0,
                    "days_late": late,
                }
            )
        return {"member": member, "loans": rows}

    def overdue(self, today: date) -> dict:
        late = [loan for loan in self.loans.values() if loan.days_late(today) > 0]
        late.sort(key=lambda loan: (-loan.days_late(today), loan.copy))
        return {
            "overdue": [
                {
                    "copy": loan.copy,
                    "member": loan.member,
                    "due": loan.due.isoformat(),
                    "days_late": loan.days_late(today),
                }
                for loan in late
            ]
        }

    # ----------------------------------------------------------------- lookups

    def _member(self, member: str) -> Member:
        if member not in self.members:
            raise Refused("unknown-member", f"no member {member}")
        return self.members[member]

    def _copy(self, copy: str) -> Copy:
        if copy not in self.copies:
            raise Refused("unknown-copy", f"no copy {copy}")
        return self.copies[copy]

    def _out(self, copy: str) -> Loan:
        self._copy(copy)
        if copy not in self.loans:
            raise Refused("not-on-loan", f"copy {copy} is not on loan")
        return self.loans[copy]

    @staticmethod
    def _loan(loan: Loan) -> dict:
        return {
            "copy": loan.copy,
            "member": loan.member,
            "lent": loan.lent.isoformat(),
            "due": loan.due.isoformat(),
            "renewals": loan.renewals,
        }


# Every operation, by the name the scenario vectors and the HTTP routes use: its handler, the
# arguments it takes, and whether it depends on the date.
OPERATIONS = {
    "add_title": (Library.add_title, ("isbn", "title", "author"), False),
    "add_copy": (Library.add_copy, ("copy", "isbn"), False),
    "add_member": (Library.add_member, ("member", "name"), False),
    "copy": (Library.copy_status, ("copy",), False),
    "lend": (Library.lend, ("member", "copy"), True),
    "renew": (Library.renew, ("copy",), True),
    "return": (Library.give_back, ("copy",), True),
    "loans": (Library.loans_of, ("member",), True),
    "overdue": (Library.overdue, (), True),
}


def apply(library: Library, op: str, args: dict, today: date) -> dict:
    """Run one named operation. Refuses an unknown operation, a missing argument and an
    argument the operation does not take, rather than guessing."""
    if op not in OPERATIONS:
        raise Refused("bad-request", f"no operation {op!r}")
    handler, names, dated = OPERATIONS[op]
    extra = sorted(set(args) - set(names))
    if extra:
        raise Refused("bad-request", f"{op} takes no {', '.join(extra)}")
    missing = [n for n in names if not isinstance(args.get(n), str) or not args[n]]
    if missing:
        raise Refused("bad-request", f"{op} needs {', '.join(missing)} (non-empty strings)")
    values = [args[n] for n in names]
    return handler(library, *values, today) if dated else handler(library, *values)
