use std::{env, fs, path::PathBuf};

fn main() {
    println!("cargo:rerun-if-env-changed=RUSTDESK_API_TLS_SOURCE");
    let source = env::var_os("RUSTDESK_API_TLS_SOURCE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap())
            .join("../../client/src/api_tls.rs"))
        .canonicalize()
        .expect("RUSTDESK_API_TLS_SOURCE must identify the reviewed production src/api_tls.rs");
    println!("cargo:rerun-if-changed={}", source.display());
    // Compile the unchanged production file. The second include binds metadata to
    // the exact bytes consumed by this build, rather than hashing a later checkout.
    let generated = format!(
        "#[path = {0:?}] mod api_tls;\nconst PRODUCTION_SOURCE: &[u8] = include_bytes!({0:?});\n",
        source.to_str().expect("UTF-8 production source path"),
    );
    fs::write(PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("production_source.rs"), generated)
        .expect("write generated source import");
}
