Run the real production account-TLS helper against loopback fixtures

Files
  harness/             Small Rust executable; imports production src/api_tls.rs.
  run.py               Python stdlib TLS fixtures and nine integration cases.
  PLAN.txt             Agreed interface, assertions and claim boundaries.
  fixture-*-check.json  Local fixture-only results, NOT production-helper results.

Build on an ephemeral Linux runner with Rust, Python 3, OpenSSL CLI, pkg-config,
and OpenSSL development headers (reqwest features match the production manifest).
No Flutter, Android SDK, desktop libraries, Docker, sudo, or real server is needed.

Example from a client checkout where these files were copied to validation/tls:

  RUSTDESK_API_TLS_SOURCE="$PWD/src/api_tls.rs" \
    cargo build --manifest-path validation/tls/harness/Cargo.toml --release
  python3 validation/tls/run.py \
    --binary "$PWD/validation/tls/harness/target/release/rustdesk-api-tls-validation" \
    --source "$PWD/src/api_tls.rs" \
    --report "$PWD/validation-results/account-tls.json"

For the current workspace, point --manifest-path at
work/stage3/tls-validation/harness/Cargo.toml and RUSTDESK_API_TLS_SOURCE at the
absolute work/stage3/client/src/api_tls.rs. The harness default source path also
supports this workspace layout. The explicit source variable is recommended in CI.

The first Cargo build resolves the standalone crate's transitive lockfile; retain
that Cargo.lock for subsequent CI use with --locked. Direct dependency versions
are fixed to the reviewed production lock (reqwest 0.12.24, Tokio 1.44.2, url 2.5.2,
serde_json 1.0.118, sha2 0.10.8). No source download script runs during validation.

Tests
  1. Exact origin selects helper; a valid private CA/IP certificate permits POST,
     headers/body reach the fixture, and one real HTTPS response is returned.
  2. Another CA is rejected, including when supplied through SSL_CERT_FILE.
  3. A trusted certificate for another IP is rejected.
  4. An expired trusted certificate is rejected.
  5. HTTP instead of configured HTTPS: classification errors, request never opens
     a connection, private trust is not offered.
  6. Same host with another port: same rejection before a connection.
  7. Another host spelling (localhost instead of IP): classification is false,
     and direct private-helper request still rejects before a connection.
  8. A 302 redirect returns as 302; its HTTP target receives zero connections.
  9. An environment proxy is not used; only the configured direct TLS endpoint
     receives the request.

Certificate failures require exactly one observed TLS connection, zero application
HTTP, zero plaintext attempts, and a certificate-related error from the actual
Rust/reqwest chain. We do not count an unreachable listener as a certificate pass.
The other-origin and redirect cases additionally observe destination listeners.

All certificates/keys are generated in a private temporary directory by a fixed
reviewed OpenSSL command sequence. Every listener binds 127.0.0.1 with an ephemeral
port. No production key/certificate is read, no external address is accepted by
the Python CLI, no system trust is installed, and process proxy variables are
removed except in the explicit synthetic proxy test. Temp keys and listener
threads are removed at completion. Reports contain counters, hashes and synthetic
errors; they contain no private keys or account credentials.

Each Rust result contains the SHA-256 of the production source bytes included at
build time. Python compares it with the requested current source, so an old binary
cannot silently validate an edited helper. The production source is compiled by
#[path], not copied or reimplemented. The report marks real_production_helper=true,
real_tls=true and actualClient=false for this standalone-helper integration run.

Fixture-only control (safe without Rust; does NOT test the production helper):
  python3 run.py --fixtures-only --report fixture-self-check.json
This invokes openssl verify for valid, wrong-CA, wrong-IP and expired fixtures.
Local fixture-runtime-self-check.json additionally records a Python ssl control
against the same local fixtures, again real_production_helper=false.

Current execution status
  Python syntax: passed.
  OpenSSL fixture checks: passed locally.
  Python ssl fixture/control requests: all four passed locally.
  Production Rust helper executable: pending Linux CI build/run.

The full RustDesk UI binaries and the three common.rs wrapper functions are not
executed by this small crate. Their source integration is reviewed separately:
the wrappers must classify the account endpoint and return helper success/error
before reaching the legacy cache, invalid-certificate, or raw-TCP retry branches.
Passing this suite must not be described as end-to-end desktop/mobile acceptance.
