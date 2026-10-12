//! The library-lending service: the rules (`domain`), an HTTP JSON API over them (`http`),
//! and the two things they are built on, JSON (`json`) and dates (`date`).
//!
//! No dependencies: the standard library is enough for all four, which is what lets
//! `cargo test` run with no network and no lock file to resolve.

pub mod date;
pub mod domain;
pub mod http;
pub mod json;
