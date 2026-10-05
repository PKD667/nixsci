//! Record experiment data in the nix-lab format (see `SPEC.md`), from Rust.
//!
//! ```no_run
//! lab::record("loss", &serde_json::json!({"epoch": 3, "value": 0.25}))?;
//! lab::record_bytes("weights", &[1, 2, 3], "application/octet-stream")?;
//! let epsilon = lab::params()["epsilon"].as_f64();
//! # Ok::<(), lab::Error>(())
//! ```
//!
//! Behaviour matches the Python module: when the run declares datasets
//! (`NIX_LAB_SCHEMA`), a JSON value must be a declared dataset's row and is checked
//! against its columns and key (`NIX_LAB_KEYS`); artifacts are always free.

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

const VERSION: u64 = 1;

#[derive(Debug)]
pub enum Error {
    /// The value does not fit the run's declared datasets.
    Invalid(String),
    Io(std::io::Error),
    Json(serde_json::Error),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Invalid(m) => write!(f, "{m}"),
            Error::Io(e) => write!(f, "{e}"),
            Error::Json(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e)
    }
}
impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Error::Json(e)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Int,
    Float,
    Str,
    Bool,
}

struct Column {
    name: String,
    kind: Kind,
    nullable: bool,
}

type Schemas = HashMap<String, Vec<Column>>;

fn parse_schemas() -> Schemas {
    let Ok(raw) = std::env::var("NIX_LAB_SCHEMA") else {
        return HashMap::new();
    };
    let parsed: HashMap<String, HashMap<String, String>> =
        serde_json::from_str(&raw).expect("NIX_LAB_SCHEMA is not valid JSON");
    parsed
        .into_iter()
        .map(|(dataset, columns)| {
            let mut columns: Vec<Column> = columns
                .into_iter()
                .map(|(name, spec)| {
                    let nullable = spec.ends_with('?');
                    let kind = match spec.trim_end_matches('?') {
                        "int" => Kind::Int,
                        "float" => Kind::Float,
                        "str" => Kind::Str,
                        "bool" => Kind::Bool,
                        other => panic!("dataset {dataset:?}: column {name:?} has unknown type {other:?}"),
                    };
                    Column { name, kind, nullable }
                })
                .collect();
            columns.sort_by(|a, b| a.name.cmp(&b.name));
            (dataset, columns)
        })
        .collect()
}

fn schemas() -> &'static Schemas {
    static S: OnceLock<Schemas> = OnceLock::new();
    S.get_or_init(parse_schemas)
}

fn keys() -> &'static HashMap<String, Vec<String>> {
    static K: OnceLock<HashMap<String, Vec<String>>> = OnceLock::new();
    K.get_or_init(|| match std::env::var("NIX_LAB_KEYS") {
        Ok(raw) => serde_json::from_str(&raw).expect("NIX_LAB_KEYS is not valid JSON"),
        Err(_) => HashMap::new(),
    })
}

fn check_row(name: &str, columns: &[Column], row: &Value) -> Result<()> {
    let object = row.as_object().ok_or_else(|| {
        Error::Invalid(format!("dataset {name:?} takes a row (JSON object), got {row}"))
    })?;
    let extra: Vec<&String> = object.keys().filter(|k| !columns.iter().any(|c| &c.name == *k)).collect();
    let missing: Vec<&str> = columns
        .iter()
        .filter(|c| !object.contains_key(&c.name))
        .map(|c| c.name.as_str())
        .collect();
    if !extra.is_empty() || !missing.is_empty() {
        return Err(Error::Invalid(format!(
            "dataset {name:?}: unexpected columns {extra:?}, missing {missing:?}"
        )));
    }
    for column in columns {
        let value = &object[&column.name];
        let fits = match column.kind {
            Kind::Int => value.is_i64() || value.is_u64(),
            Kind::Float => value.is_number(),
            Kind::Str => value.is_string(),
            Kind::Bool => value.is_boolean(),
        };
        if value.is_null() {
            if !column.nullable {
                return Err(Error::Invalid(format!(
                    "dataset {name:?}: column {:?} is not nullable",
                    column.name
                )));
            }
        } else if !fits {
            return Err(Error::Invalid(format!(
                "dataset {name:?}: column {:?} has the wrong type: {value}",
                column.name
            )));
        }
    }
    Ok(())
}

fn seen() -> &'static Mutex<HashMap<String, HashSet<String>>> {
    static S: OnceLock<Mutex<HashMap<String, HashSet<String>>>> = OnceLock::new();
    S.get_or_init(|| Mutex::new(HashMap::new()))
}

fn dir() -> Result<PathBuf> {
    let root = std::env::var("NIX_LAB_DIR").unwrap_or_else(|_| "lab".into());
    let path = PathBuf::from(root);
    fs::create_dir_all(path.join("artifacts"))?;
    Ok(path)
}

/// UTC time as `YYYY-MM-DDTHH:MM:SS.ffffffZ`, the format the records use.
fn now() -> String {
    let d = SystemTime::now().duration_since(UNIX_EPOCH).expect("clock before 1970");
    let (secs, micros) = (d.as_secs() as i64, d.subsec_micros());
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{micros:06}Z",
        rem / 3_600,
        rem % 3_600 / 60,
        rem % 60
    )
}

fn entry(name: &str, tags: &Map<String, Value>) -> Map<String, Value> {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let mut e = Map::new();
    e.insert("v".into(), json!(VERSION));
    e.insert("id".into(), json!(format!("{}-{}", std::process::id(), COUNTER.fetch_add(1, Ordering::Relaxed))));
    e.insert("time".into(), json!(now()));
    e.insert("name".into(), json!(name));
    e.insert("tags".into(), Value::Object(tags.clone()));
    e
}

fn append(entry: Map<String, Value>) -> Result<()> {
    let mut line = serde_json::to_vec(&Value::Object(entry))?;
    line.push(b'\n');
    // One write(2) of a whole line on an O_APPEND file: concurrent writers do not interleave.
    let mut file = OpenOptions::new().create(true).append(true).open(dir()?.join("records.jsonl"))?;
    file.write_all(&line)?;
    Ok(())
}

/// Record a JSON-serialisable value under `name`.
pub fn record<T: serde::Serialize>(name: &str, value: &T) -> Result<()> {
    record_tagged(name, value, &Map::new())
}

/// Like [`record`], with free-form JSON tags.
pub fn record_tagged<T: serde::Serialize>(name: &str, value: &T, tags: &Map<String, Value>) -> Result<()> {
    if name.is_empty() {
        return Err(Error::Invalid("record name must be non-empty".into()));
    }
    let value = serde_json::to_value(value)?;
    let declared = schemas();
    if !declared.is_empty() {
        let columns = declared.get(name).ok_or_else(|| {
            let mut names: Vec<&String> = declared.keys().collect();
            names.sort();
            Error::Invalid(format!("{name:?} is not a declared dataset; declared: {names:?}"))
        })?;
        check_row(name, columns, &value)?;
        if let Some(key) = keys().get(name) {
            let ident = Value::Array(key.iter().map(|c| value[c].clone()).collect()).to_string();
            let mut seen = seen().lock().unwrap();
            if !seen.entry(name.to_string()).or_default().insert(ident.clone()) {
                return Err(Error::Invalid(format!("dataset {name:?}: duplicate key {ident}")));
            }
        }
    }
    let mut e = entry(name, tags);
    e.insert("kind".into(), json!("value"));
    e.insert("data".into(), value);
    append(e)
}

/// Record a blob as a content-addressed artifact.
pub fn record_bytes(name: &str, bytes: &[u8], media: &str) -> Result<()> {
    if name.is_empty() {
        return Err(Error::Invalid("record name must be non-empty".into()));
    }
    let digest = format!("{:x}", Sha256::digest(bytes));
    let artifacts = dir()?.join("artifacts");
    let target = artifacts.join(&digest);
    if !target.exists() {
        let tmp = artifacts.join(format!(".{digest}.{}", std::process::id()));
        fs::write(&tmp, bytes)?;
        fs::rename(&tmp, &target)?;
    }
    let mut e = entry(name, &Map::new());
    e.insert("kind".into(), json!("artifact"));
    e.insert("sha256".into(), json!(digest));
    e.insert("bytes".into(), json!(bytes.len()));
    e.insert("media".into(), json!(media));
    append(e)
}

/// This run's parameters (`NIX_LAB_PARAMS`), an empty object when unset.
pub fn params() -> Value {
    std::env::var("NIX_LAB_PARAMS")
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_else(|| json!({}))
}

/// This run's seed (`NIX_LAB_SEED`).
pub fn seed() -> Option<i64> {
    std::env::var("NIX_LAB_SEED").ok()?.parse().ok()
}
