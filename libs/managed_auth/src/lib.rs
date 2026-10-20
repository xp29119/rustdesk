//! Device-bound authentication. One background service owns a Broker per device.
//! The UI and transport processes request operations through authenticated local IPC.
use anyhow::{bail, ensure, Context, Result};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use ed25519_dalek::{Signer, SigningKey};
use rand::rngs::OsRng;
use reqwest::{redirect::Policy, Client, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fmt,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::sync::Mutex;
use zeroize::{Zeroize, ZeroizeOnDrop, Zeroizing};

#[cfg(any(target_os = "macos", target_os = "windows"))]
pub mod desktop_vault;

/// Implementations must use OS-protected storage and atomic replacement.
/// A missing entry is None; locked/unavailable/corrupt storage is an error.
pub trait Vault: Send + Sync {
    fn load(&self) -> Result<Option<Vec<u8>>>;
    fn save(&self, value: &[u8]) -> Result<()>;
}

#[derive(Serialize, Deserialize, Zeroize, ZeroizeOnDrop)]
struct Stored {
    version: u32,
    seed: [u8; 32],
    username: String,
    device_id: String,
    refresh_token: Option<String>,
}

impl Stored {
    fn new() -> Self {
        Self {
            version: 1,
            seed: SigningKey::generate(&mut OsRng).to_bytes(),
            username: String::new(),
            device_id: String::new(),
            refresh_token: None,
        }
    }

    fn key(&self) -> SigningKey {
        SigningKey::from_bytes(&self.seed)
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case")]
pub enum Command {
    Status,
    Login {
        username: String,
        password: String,
        device_name: String,
    },
    Logout,
    Headers {
        url: String,
    },
}

impl fmt::Debug for Command {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Status => "ManagedStatus",
            Self::Login { .. } => "ManagedLogin([redacted])",
            Self::Logout => "ManagedLogout",
            Self::Headers { .. } => "ManagedHeaders",
        })
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Status {
    pub state: String,
    pub username: String,
    pub device_id: String,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Response {
    Status(Status),
    Headers { authorization: String, dpop: String },
}

impl fmt::Debug for Response {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Status(s) => s.fmt(f),
            Self::Headers { .. } => f.write_str("ManagedHeaders([redacted])"),
        }
    }
}

#[derive(Deserialize, Zeroize, ZeroizeOnDrop)]
struct Credentials {
    device_id: String,
    device_status: String,
    refresh_token: String,
    access_token: Option<String>,
    expires_in: u64,
}

struct Session {
    stored: Stored,
    access: Option<Zeroizing<String>>,
    expires_at: u64,
    checked_at: u64,
    state: String,
}

pub struct Broker {
    origin: String,
    platform: String,
    http: Client,
    vault: Arc<dyn Vault>,
    session: Mutex<Option<Session>>,
}

fn timestamp() -> Result<u64> {
    Ok(SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs())
}

fn proof(key: &SigningKey, method: &str, url: &str, token: Option<&str>) -> Result<String> {
    let header = json!({
        "typ": "dpop+jwt", "alg": "EdDSA",
        "jwk": {"kty": "OKP", "crv": "Ed25519",
                "x": URL_SAFE_NO_PAD.encode(key.verifying_key().as_bytes())}
    });
    let mut payload = json!({
        "jti": uuid::Uuid::new_v4().to_string(), "iat": timestamp()?,
        "htm": method, "htu": url
    });
    if let Some(token) = token {
        payload["ath"] = URL_SAFE_NO_PAD
            .encode(Sha256::digest(token.as_bytes()))
            .into();
    }
    let message = format!(
        "{}.{}",
        URL_SAFE_NO_PAD.encode(serde_json::to_vec(&header)?),
        URL_SAFE_NO_PAD.encode(serde_json::to_vec(&payload)?)
    );
    let signature = key.sign(message.as_bytes());
    Ok(format!(
        "{}.{}",
        message,
        URL_SAFE_NO_PAD.encode(signature.to_bytes())
    ))
}

impl Broker {
    pub fn new(origin: &str, platform: &str, vault: Arc<dyn Vault>) -> Result<Self> {
        let url = reqwest::Url::parse(origin)?;
        ensure!(
            url.scheme() == "https"
                && url.host_str().is_some()
                && url.username().is_empty()
                && url.password().is_none()
                && url.path() == "/"
                && url.query().is_none()
                && url.fragment().is_none(),
            "Invalid managed service origin"
        );
        ensure!(
            matches!(platform, "windows" | "macos" | "android"),
            "Unsupported platform"
        );
        let http = Client::builder()
            .https_only(true)
            .redirect(Policy::none())
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(20))
            .build()?;
        Ok(Self {
            origin: origin.trim_end_matches('/').to_owned(),
            platform: platform.into(),
            http,
            vault,
            session: Mutex::new(None),
        })
    }

    /// Trust only this application's dedicated CA; keep hostname, expiry and chain verification.
    pub fn new_with_tls_root(
        origin: &str,
        platform: &str,
        vault: Arc<dyn Vault>,
        root_pem: &[u8],
    ) -> Result<Self> {
        let roots = reqwest::Certificate::from_pem_bundle(root_pem)?;
        ensure!(roots.len() == 1, "Expected exactly one dedicated root certificate");
        let root = roots.into_iter().next().context("Missing dedicated root certificate")?;
        let mut broker = Self::new(origin, platform, vault)?;
        broker.http = Client::builder()
            .https_only(true)
            .redirect(Policy::none())
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(20))
            .tls_built_in_root_certs(false)
            .add_root_certificate(root)
            .build()?;
        Ok(broker)
    }

    async fn load(&self) -> Result<Session> {
        let vault = self.vault.clone();
        let raw = tokio::task::spawn_blocking(move || vault.load()).await??;
        let stored = match raw {
            Some(raw) => {
                let raw = Zeroizing::new(raw);
                let stored: Stored = serde_json::from_slice(&raw)
                    .context("Saved login could not be read; protected storage was not replaced")?;
                ensure!(stored.version == 1, "Unsupported saved login version");
                stored
            }
            None => {
                let stored = Stored::new();
                self.save(&stored).await?;
                stored
            }
        };
        Ok(Session {
            state: if stored.refresh_token.is_some() {
                "restoring"
            } else {
                "signed_out"
            }
            .into(),
            stored,
            access: None,
            expires_at: 0,
            checked_at: 0,
        })
    }

    async fn save(&self, stored: &Stored) -> Result<()> {
        let data = Zeroizing::new(serde_json::to_vec(stored)?);
        let vault = self.vault.clone();
        tokio::task::spawn_blocking(move || vault.save(&data)).await??;
        Ok(())
    }

    async fn request(
        &self,
        session: &Session,
        path: &str,
        body: Value,
        token: Option<&str>,
    ) -> Result<reqwest::Response> {
        let url = format!("{}{}", self.origin, path);
        let proof = proof(&session.stored.key(), "POST", &url, token)?;
        self.http
            .post(url)
            .header("DPoP", proof)
            .json(&body)
            .send()
            .await
            .context("无法安全连接登录服务，请检查网络和系统时间")
    }

    async fn accept(&self, s: &mut Session, response: reqwest::Response) -> Result<()> {
        match response.status() {
            StatusCode::OK | StatusCode::ACCEPTED => {}
            StatusCode::UNAUTHORIZED => bail!("登录已失效或账号密码错误，请重新登录"),
            StatusCode::FORBIDDEN => bail!("账号或设备未获授权，请联系管理员"),
            StatusCode::TOO_MANY_REQUESTS => bail!("尝试过于频繁，请稍后重试"),
            _ => bail!("登录服务暂时不可用"),
        }
        let body: Credentials = response
            .json()
            .await
            .map_err(|_| anyhow::anyhow!("登录服务返回了无效响应"))?;
        ensure!(
            matches!(body.device_status.as_str(), "pending" | "approved")
                && (40..=128).contains(&body.refresh_token.len())
                && !body.device_id.is_empty()
                && body.device_id.len() <= 128
                && body.expires_in > 0
                && body.expires_in <= 300
                && (body.device_status != "approved"
                    || body
                        .access_token
                        .as_ref()
                        .is_some_and(|t| !t.is_empty() && t.len() < 4096)),
            "Invalid login response"
        );
        s.stored.device_id.clone_from(&body.device_id);
        s.stored.refresh_token = Some(body.refresh_token.clone());
        // Publish access only after the replacement refresh credential is durably saved.
        if let Err(error) = self.save(&s.stored).await {
            s.stored.refresh_token = None;
            s.access = None;
            s.state = "signed_out".into();
            return Err(error.context("Could not securely save login"));
        }
        s.access = body
            .access_token
            .as_ref()
            .map(|token| Zeroizing::new(token.clone()));
        s.state.clone_from(&body.device_status);
        s.checked_at = timestamp()?;
        s.expires_at = s.checked_at + body.expires_in;
        Ok(())
    }

    async fn refresh(&self, s: &mut Session) -> Result<()> {
        let Some(token) = s.stored.refresh_token.take().map(Zeroizing::new) else {
            s.access = None;
            s.state = "signed_out".into();
            return Ok(());
        };
        // Never retry a rotating credential after a crash or an ambiguous network failure.
        // Persist its removal before sending; a failed rotation requires a new login.
        if let Err(error) = self.save(&s.stored).await {
            s.stored.refresh_token = Some(token.to_string());
            return Err(error);
        }
        s.access = None;
        s.state = "signed_out".into();
        let response = self
            .request(
                s,
                "/api/auth/refresh",
                json!({"refresh_token": token.as_str()}),
                Some(&token),
            )
            .await;
        let response = match response {
            Ok(response) => response,
            Err(error) => {
                // A connection failure precedes the HTTP request. Preserve login for offline
                // startup, but never restore a token after an ambiguous response timeout.
                if error
                    .downcast_ref::<reqwest::Error>()
                    .is_some_and(|e| e.is_connect())
                {
                    s.stored.refresh_token = Some(token.to_string());
                    s.state = "restoring".into();
                    self.save(&s.stored).await?;
                }
                return Err(error);
            }
        };
        self.accept(s, response).await
    }

    fn status(s: &Session) -> Response {
        Response::Status(Status {
            state: s.state.clone(),
            username: s.stored.username.clone(),
            device_id: s.stored.device_id.clone(),
        })
    }

    /// The mutex serializes UI, signaling and relay requests within the credential owner.
    /// Do not create a Broker in every desktop process; route all callers to the service.
    pub async fn execute(&self, command: Command) -> Result<Response> {
        let mut guard = self.session.lock().await;
        if guard.is_none() {
            *guard = Some(self.load().await?);
        }
        let s = guard.as_mut().context("Login state unavailable")?;
        match command {
            Command::Login {
                username,
                password,
                device_name,
            } => {
                let password = Zeroizing::new(password);
                ensure!(
                    (3..=40).contains(&username.len()) && password.len() <= 256,
                    "请输入有效的账号和密码"
                );
                ensure!(
                    (1..=80).contains(&device_name.chars().count()),
                    "请填写设备名称"
                );
                ensure!(
                    s.stored.username.is_empty() || s.stored.username.eq_ignore_ascii_case(&username),
                    "此设备已绑定其他账号，请联系管理员"
                );
                s.access = None;
                s.state = "signed_out".into();
                s.stored.refresh_token = None;
                self.save(&s.stored).await?;
                let response = self.request(s, "/api/auth/login", json!({
                    "username": username, "password": password.as_str(),
                    "device_name": device_name, "platform": self.platform,
                    "public_key": URL_SAFE_NO_PAD.encode(s.stored.key().verifying_key().as_bytes())
                }), None).await?;
                // Only bind the local account after a successful server response.
                if matches!(response.status(), StatusCode::OK | StatusCode::ACCEPTED) {
                    s.stored.username = username;
                }
                self.accept(s, response).await?;
                Ok(Self::status(s))
            }
            Command::Logout => {
                let refresh = s.stored.refresh_token.take().map(Zeroizing::new);
                s.access = None;
                s.state = "signed_out".into();
                self.save(&s.stored).await?;
                if let Some(token) = refresh {
                    let response = self
                        .request(
                            s,
                            "/api/auth/logout",
                            json!({"refresh_token": token.as_str()}),
                            Some(&token),
                        )
                        .await?;
                    ensure!(
                        response.status().is_success()
                            || response.status() == StatusCode::UNAUTHORIZED,
                        "本机已退出；服务器退出未确认，请在后台撤销设备"
                    );
                }
                Ok(Self::status(s))
            }
            Command::Status => {
                let now = timestamp()?;
                let due = s.state == "restoring"
                    || (s.state == "pending" && now >= s.checked_at + 30)
                    || (s.state == "approved" && now + 30 >= s.expires_at);
                if due {
                    self.refresh(s).await?;
                }
                Ok(Self::status(s))
            }
            Command::Headers { url } => {
                let id = self.origin.replacen("https://", "wss://", 1) + "/ws/id";
                let relay = self.origin.replacen("https://", "wss://", 1) + "/ws/relay";
                ensure!(
                    url == id || url == relay,
                    "Unapproved managed transport destination"
                );
                if s.access.is_none() || timestamp()? + 30 >= s.expires_at {
                    self.refresh(s).await?;
                }
                ensure!(s.state == "approved", "请先登录并等待管理员批准此设备");
                let access = s.access.as_ref().context("请重新登录")?;
                let proof = proof(
                    &s.stored.key(),
                    "GET",
                    &url.replacen("wss://", "https://", 1),
                    Some(access),
                )?;
                Ok(Response::Headers {
                    authorization: format!("Bearer {}", access.as_str()),
                    dpop: proof,
                })
            }
        }
    }
}

#[cfg(test)]
mod tests;
