//! Writes a small run the way an experiment would; the Python test reads it back.
fn main() -> Result<(), lab::Error> {
    let epsilon = lab::params()["epsilon"].as_f64().unwrap_or(0.1);
    for epoch in 0..3 {
        lab::record(
            "loss",
            &serde_json::json!({"epoch": epoch, "split": "train", "value": epsilon * 0.5f64.powi(epoch)}),
        )?;
    }
    lab::record_bytes("blob", b"abc", "application/octet-stream")?;
    // A repeated key must be refused, and nothing written for it.
    assert!(lab::record("loss", &serde_json::json!({"epoch": 0, "split": "train", "value": 9.0})).is_err());
    // So must a row that does not fit.
    assert!(lab::record("loss", &serde_json::json!({"epoch": 1.5, "split": "val", "value": 1.0})).is_err());
    println!("seed={:?}", lab::seed());
    Ok(())
}
