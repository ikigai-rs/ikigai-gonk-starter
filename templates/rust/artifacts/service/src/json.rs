//! JSON, standard library only: the values the API serves and the scenario vectors speak in.
//!
//! An object is a `BTreeMap`, so two objects with the same members are equal whatever order
//! they were written in, and print with their keys sorted: one canonical form on both sides
//! of a comparison, which is what lets a vector's `expect` be compared to an answer.

use std::collections::BTreeMap;
use std::fmt::Write as _;

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Array(Vec<Value>),
    Object(BTreeMap<String, Value>),
}

impl Value {
    /// An object from its members, in any order.
    pub fn object(members: Vec<(&str, Value)>) -> Value {
        Value::Object(
            members
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
        )
    }

    pub fn string(text: &str) -> Value {
        Value::Str(text.to_string())
    }

    pub fn integer(number: i64) -> Value {
        Value::Int(number)
    }

    pub fn boolean(flag: bool) -> Value {
        Value::Bool(flag)
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Object(members) => members.get(key),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(text) => Some(text),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&Vec<Value>> {
        match self {
            Value::Array(items) => Some(items),
            _ => None,
        }
    }

    pub fn as_object(&self) -> Option<&BTreeMap<String, Value>> {
        match self {
            Value::Object(members) => Some(members),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Value::Bool(flag) => Some(*flag),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Value::Int(number) => Some(*number),
            _ => None,
        }
    }
}

/// Parse one JSON value, refusing anything else (there is no trailing text to allow).
pub fn parse(text: &str) -> Result<Value, String> {
    let mut parser = Parser {
        chars: text.chars().collect(),
        at: 0,
    };
    parser.skip_whitespace();
    let value = parser.value()?;
    parser.skip_whitespace();
    if parser.at < parser.chars.len() {
        return Err(parser.error("trailing text"));
    }
    Ok(value)
}

/// Print one JSON value: compact, keys sorted, non-ASCII as itself.
pub fn to_text(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value);
    out
}

fn write_value(out: &mut String, value: &Value) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(number) => {
            let _ = write!(out, "{number}");
        }
        Value::Float(number) => {
            // JSON has no NaN and no infinity; a number that is not finite is null.
            if !number.is_finite() {
                out.push_str("null");
            } else if number.fract() == 0.0 {
                let _ = write!(out, "{number:.1}");
            } else {
                let _ = write!(out, "{number}");
            }
        }
        Value::Str(text) => write_string(out, text),
        Value::Array(items) => {
            out.push('[');
            for (n, item) in items.iter().enumerate() {
                if n > 0 {
                    out.push(',');
                }
                write_value(out, item);
            }
            out.push(']');
        }
        Value::Object(members) => {
            out.push('{');
            for (n, (key, member)) in members.iter().enumerate() {
                if n > 0 {
                    out.push(',');
                }
                write_string(out, key);
                out.push(':');
                write_value(out, member);
            }
            out.push('}');
        }
    }
}

fn write_string(out: &mut String, text: &str) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

struct Parser {
    chars: Vec<char>,
    at: usize,
}

impl Parser {
    fn error(&self, what: &str) -> String {
        format!("at character {}: {what}", self.at)
    }

    fn peek(&self) -> Option<char> {
        self.chars.get(self.at).copied()
    }

    fn bump(&mut self) -> Option<char> {
        let found = self.peek();
        if found.is_some() {
            self.at += 1;
        }
        found
    }

    fn skip_whitespace(&mut self) {
        while matches!(self.peek(), Some(' ' | '\t' | '\n' | '\r')) {
            self.at += 1;
        }
    }

    fn expect(&mut self, word: &str) -> Result<(), String> {
        for want in word.chars() {
            if self.bump() != Some(want) {
                return Err(self.error(&format!("expected {word}")));
            }
        }
        Ok(())
    }

    fn value(&mut self) -> Result<Value, String> {
        match self.peek() {
            Some('{') => self.object(),
            Some('[') => self.array(),
            Some('"') => Ok(Value::Str(self.string()?)),
            Some('t') => self.literal("true", Value::Bool(true)),
            Some('f') => self.literal("false", Value::Bool(false)),
            Some('n') => self.literal("null", Value::Null),
            Some(c) if c == '-' || c.is_ascii_digit() => self.number(),
            Some(c) => Err(self.error(&format!("unexpected {c:?}"))),
            None => Err(self.error("the text ended")),
        }
    }

    fn literal(&mut self, word: &str, value: Value) -> Result<Value, String> {
        self.expect(word)?;
        Ok(value)
    }

    fn object(&mut self) -> Result<Value, String> {
        self.bump(); // {
        let mut members = BTreeMap::new();
        self.skip_whitespace();
        if self.peek() == Some('}') {
            self.bump();
            return Ok(Value::Object(members));
        }
        loop {
            self.skip_whitespace();
            if self.peek() != Some('"') {
                return Err(self.error("expected a member's name, a string"));
            }
            let key = self.string()?;
            self.skip_whitespace();
            if self.bump() != Some(':') {
                return Err(self.error("expected : after a member's name"));
            }
            self.skip_whitespace();
            let value = self.value()?;
            members.insert(key, value);
            self.skip_whitespace();
            match self.bump() {
                Some(',') => continue,
                Some('}') => return Ok(Value::Object(members)),
                _ => return Err(self.error("expected , or }")),
            }
        }
    }

    fn array(&mut self) -> Result<Value, String> {
        self.bump(); // [
        let mut items = Vec::new();
        self.skip_whitespace();
        if self.peek() == Some(']') {
            self.bump();
            return Ok(Value::Array(items));
        }
        loop {
            self.skip_whitespace();
            items.push(self.value()?);
            self.skip_whitespace();
            match self.bump() {
                Some(',') => continue,
                Some(']') => return Ok(Value::Array(items)),
                _ => return Err(self.error("expected , or ]")),
            }
        }
    }

    fn string(&mut self) -> Result<String, String> {
        self.bump(); // the opening quote
        let mut out = String::new();
        loop {
            match self.bump() {
                None => return Err(self.error("the string never ended")),
                Some('"') => return Ok(out),
                Some('\\') => match self.bump() {
                    Some('"') => out.push('"'),
                    Some('\\') => out.push('\\'),
                    Some('/') => out.push('/'),
                    Some('b') => out.push('\u{8}'),
                    Some('f') => out.push('\u{c}'),
                    Some('n') => out.push('\n'),
                    Some('r') => out.push('\r'),
                    Some('t') => out.push('\t'),
                    Some('u') => out.push(self.escaped_code_point()?),
                    _ => return Err(self.error("an escape that is not one of \" \\ / b f n r t u")),
                },
                Some(c) if (c as u32) < 0x20 => {
                    return Err(self.error("a control character, which a string may not hold"))
                }
                Some(c) => out.push(c),
            }
        }
    }

    fn escaped_code_point(&mut self) -> Result<char, String> {
        let first = self.hex4()?;
        if (0xd800..0xdc00).contains(&first) {
            // The first half of a surrogate pair; the second must follow as \uXXXX.
            if self.bump() != Some('\\') || self.bump() != Some('u') {
                return Err(self.error("half a surrogate pair, with nothing after it"));
            }
            let second = self.hex4()?;
            if !(0xdc00..0xe000).contains(&second) {
                return Err(self.error("half a surrogate pair, with the wrong other half"));
            }
            let point = 0x10000 + ((first - 0xd800) << 10) + (second - 0xdc00);
            return char::from_u32(point)
                .ok_or_else(|| self.error("a code point no character has"));
        }
        char::from_u32(first).ok_or_else(|| self.error("a code point no character has"))
    }

    fn hex4(&mut self) -> Result<u32, String> {
        let mut number = 0;
        for _ in 0..4 {
            let c = self.bump().ok_or_else(|| self.error("the text ended"))?;
            let digit = c
                .to_digit(16)
                .ok_or_else(|| self.error("a \\u escape that is not four hex digits"))?;
            number = number * 16 + digit;
        }
        Ok(number)
    }

    fn number(&mut self) -> Result<Value, String> {
        let start = self.at;
        if self.peek() == Some('-') {
            self.bump();
        }
        while matches!(self.peek(), Some(c) if c.is_ascii_digit()) {
            self.bump();
        }
        let mut fractional = false;
        if self.peek() == Some('.') {
            fractional = true;
            self.bump();
            while matches!(self.peek(), Some(c) if c.is_ascii_digit()) {
                self.bump();
            }
        }
        if matches!(self.peek(), Some('e' | 'E')) {
            fractional = true;
            self.bump();
            if matches!(self.peek(), Some('+' | '-')) {
                self.bump();
            }
            while matches!(self.peek(), Some(c) if c.is_ascii_digit()) {
                self.bump();
            }
        }
        let text: String = self.chars[start..self.at].iter().collect();
        if fractional {
            text.parse::<f64>()
                .map(Value::Float)
                .map_err(|_| self.error("a number that is not one"))
        } else {
            text.parse::<i64>()
                .map(Value::Int)
                .map_err(|_| self.error("a number that is not one"))
        }
    }
}
