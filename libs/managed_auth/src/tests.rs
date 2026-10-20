use super::*;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use std::{
    collections::HashSet,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Mutex as StdMutex,
    },
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
};

#[derive(Default)]
struct MemoryVault(StdMutex<Option<Vec<u8>>>);

impl Vault for MemoryVault {
    fn load(&self) -> Result<Option<Vec<u8>>> {
        Ok(self.0.lock().unwrap().clone())
    }
    fn save(&self, value: &[u8]) -> Result<()> {
        *self.0.lock().unwrap() = Some(value.to_vec());
        Ok(())
    }
}

fn verify_proof(value: &str, method: &str, url: &str, token: Option<&str>) -> Value {
    let parts: Vec<_> = value.split('.').collect();
    assert_eq!(parts.len(), 3);
    let header: Value = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(parts[0]).unwrap()).unwrap();
    let claims: Value = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(parts[1]).unwrap()).unwrap();
    assert_eq!(header["typ"], "dpop+jwt");
    assert_eq!(header["alg"], "EdDSA");
    let public = URL_SAFE_NO_PAD
        .decode(header["jwk"]["x"].as_str().unwrap())
        .unwrap();
    let key = VerifyingKey::from_bytes(&public.try_into().unwrap()).unwrap();
    let signature = Signature::from_slice(&URL_SAFE_NO_PAD.decode(parts[2]).unwrap()).unwrap();
    key.verify(format!("{}.{}", parts[0], parts[1]).as_bytes(), &signature)
        .unwrap();
    assert_eq!(claims["htm"], method);
    assert_eq!(claims["htu"], url);
    assert!(
        timestamp()
            .unwrap()
            .abs_diff(claims["iat"].as_u64().unwrap())
            < 5
    );
    if let Some(token) = token {
        assert_eq!(
            claims["ath"],
            URL_SAFE_NO_PAD.encode(Sha256::digest(token.as_bytes()))
        );
    }
    claims
}

#[test]
fn proofs_bind_key_token_method_and_destination_and_logs_redact_secrets() {
    let key = SigningKey::generate(&mut OsRng);
    let url = "https://8.138.230.183/ws/id";
    let first = proof(&key, "GET", url, Some("secret-access-token")).unwrap();
    let second = proof(&key, "GET", url, Some("secret-access-token")).unwrap();
    let a = verify_proof(&first, "GET", url, Some("secret-access-token"));
    let b = verify_proof(&second, "GET", url, Some("secret-access-token"));
    assert_ne!(a["jti"], b["jti"]);
    let command = Command::Login {
        username: "alice".into(),
        password: "never-log-password".into(),
        device_name: "desk".into(),
    };
    assert!(!format!("{command:?}").contains("never-log-password"));
    let response = Response::Headers {
        authorization: "never-log-token".into(),
        dpop: first,
    };
    assert!(!format!("{response:?}").contains("never-log-token"));
    for origin in [
        "http://8.138.230.183",
        "https://user@8.138.230.183",
        "https://8.138.230.183/path",
        "https://8.138.230.183?x=1",
    ] {
        assert!(Broker::new(origin, "macos", Arc::new(MemoryVault::default())).is_err());
    }
}

async fn mock_service() -> (String, Arc<AtomicUsize>, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let expected_origin = origin.clone();
    let rotations = Arc::new(AtomicUsize::new(0));
    let counter = rotations.clone();
    let task = tokio::spawn(async move {
        let mut valid_refresh = String::new();
        let mut nonces = HashSet::new();
        loop {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut data = Vec::new();
            let (header_end, body_size) = loop {
                let mut chunk = [0u8; 4096];
                let read = stream.read(&mut chunk).await.unwrap();
                assert!(read > 0);
                data.extend_from_slice(&chunk[..read]);
                if let Some(end) = data.windows(4).position(|w| w == b"\r\n\r\n") {
                    let headers = std::str::from_utf8(&data[..end]).unwrap().to_lowercase();
                    let size = headers
                        .lines()
                        .find_map(|line| {
                            line.strip_prefix("content-length: ")
                                .map(|s| s.parse::<usize>().unwrap())
                        })
                        .unwrap();
                    break (end + 4, size);
                }
            };
            while data.len() < header_end + body_size {
                let mut chunk = [0u8; 4096];
                let read = stream.read(&mut chunk).await.unwrap();
                assert!(read > 0);
                data.extend_from_slice(&chunk[..read]);
            }
            let headers = std::str::from_utf8(&data[..header_end]).unwrap();
            let path = headers
                .lines()
                .next()
                .unwrap()
                .split_whitespace()
                .nth(1)
                .unwrap();
            let proof = headers
                .lines()
                .find_map(|line| line.strip_prefix("dpop: "))
                .unwrap();
            let body: Value =
                serde_json::from_slice(&data[header_end..header_end + body_size]).unwrap();
            let token = if path == "/api/auth/login" {
                None
            } else {
                Some(valid_refresh.as_str())
            };
            let claims = verify_proof(proof, "POST", &(expected_origin.clone() + path), token);
            assert!(nonces.insert(claims["jti"].as_str().unwrap().to_owned()));
            if let Some(token) = token {
                assert_eq!(body["refresh_token"], token);
            }
            let count = if path == "/api/auth/refresh" {
                counter.fetch_add(1, Ordering::SeqCst) + 1
            } else {
                0
            };
            valid_refresh = format!("{count:048}");
            let response = if path == "/api/auth/logout" {
                json!({"ok": true})
            } else {
                json!({
                    "device_id": "device-test", "device_status": if count == 0 { "pending" } else { "approved" },
                    "refresh_token": valid_refresh, "expires_in": 300,
                    "access_token": if count == 0 { None } else { Some("approved-access-token") }
                })
            };
            let response = serde_json::to_string(&response).unwrap();
            stream.write_all(format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                response.len(), response
            ).as_bytes()).await.unwrap();
        }
    });
    (origin, rotations, task)
}

fn test_broker(origin: &str, vault: Arc<dyn Vault>) -> Broker {
    let mut broker = Broker::new("https://8.138.230.183", "macos", vault).unwrap();
    // HTTP exists only in this private test constructor, never in shipping code.
    broker.origin = origin.into();
    broker.http = Client::builder()
        .redirect(Policy::none())
        .timeout(Duration::from_secs(3))
        .build()
        .unwrap();
    broker
}

#[tokio::test]
async fn approval_restore_and_concurrent_refresh_use_a_single_credential_owner() {
    let (origin, rotations, server) = mock_service().await;
    let vault = Arc::new(MemoryVault::default());
    let broker = test_broker(&origin, vault.clone());
    let response = broker
        .execute(Command::Login {
            username: "alice".into(),
            password: "a-long-test-password".into(),
            device_name: "Test Mac".into(),
        })
        .await
        .unwrap();
    assert!(matches!(response, Response::Status(Status { state, .. }) if state == "pending"));
    assert!(broker
        .execute(Command::Headers {
            url: "wss://attacker.invalid/ws/id".into()
        })
        .await
        .is_err());
    drop(broker);
    let broker = Arc::new(test_broker(&origin, vault));
    let mut tasks = Vec::new();
    for _ in 0..8 {
        let broker = broker.clone();
        tasks.push(tokio::spawn(async move {
            broker.execute(Command::Status).await.unwrap()
        }));
    }
    for task in tasks {
        assert!(
            matches!(task.await.unwrap(), Response::Status(Status { state, .. }) if state == "approved")
        );
    }
    assert_eq!(rotations.load(Ordering::SeqCst), 1);
    let headers = broker
        .execute(Command::Headers {
            url: origin.clone() + "/ws/id",
        })
        .await
        .unwrap();
    if let Response::Headers {
        authorization,
        dpop,
    } = headers
    {
        assert_eq!(authorization, "Bearer approved-access-token");
        verify_proof(
            &dpop,
            "GET",
            &(origin.clone() + "/ws/id"),
            Some("approved-access-token"),
        );
    } else {
        panic!("Expected transport headers");
    }
    assert!(matches!(broker.execute(Command::Logout).await.unwrap(),
                     Response::Status(Status { state, .. }) if state == "signed_out"));
    assert!(broker
        .execute(Command::Headers {
            url: origin + "/ws/id"
        })
        .await
        .is_err());
    server.abort();
}

#[tokio::test]
async fn offline_start_preserves_refresh_but_corrupt_storage_is_never_overwritten() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    drop(listener);
    let vault = Arc::new(MemoryVault::default());
    let mut stored = Stored::new();
    stored.username = "alice".into();
    stored.refresh_token = Some("0".repeat(48));
    vault.save(&serde_json::to_vec(&stored).unwrap()).unwrap();
    let broker = test_broker(&origin, vault.clone());
    assert!(broker.execute(Command::Status).await.is_err());
    let saved: Stored = serde_json::from_slice(&vault.load().unwrap().unwrap()).unwrap();
    assert_eq!(
        saved.refresh_token.as_deref(),
        Some("0".repeat(48).as_str())
    );
    vault.save(b"corrupt private data").unwrap();
    let broker = test_broker(&origin, vault.clone());
    assert!(broker.execute(Command::Status).await.is_err());
    assert_eq!(vault.load().unwrap().unwrap(), b"corrupt private data");
}


/// Explicit deployment probe using only anonymous GETs; normal CI remains offline.
#[tokio::test]
#[ignore = "requires RDCONTROL_TLS_ORIGIN and RDCONTROL_TLS_ROOT"]
async fn dedicated_ca_https_accepts_only_configured_trust_and_https() {
    let origin = std::env::var("RDCONTROL_TLS_ORIGIN").unwrap();
    let root = std::fs::read(std::env::var("RDCONTROL_TLS_ROOT").unwrap()).unwrap();
    let broker = Broker::new_with_tls_root(&origin, "macos", Arc::new(MemoryVault::default()), &root).unwrap();
    let response = broker.http.get(format!("{origin}/api/me")).send().await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(broker.http.get(origin.replacen("https://", "http://", 1)).send().await.is_err());
    let normal = Broker::new(&origin, "macos", Arc::new(MemoryVault::default())).unwrap();
    assert!(normal.http.get(format!("{origin}/api/me")).send().await.is_err());
    assert!(Broker::new_with_tls_root(&origin, "macos", Arc::new(MemoryVault::default()), b"invalid certificate").is_err());
}
