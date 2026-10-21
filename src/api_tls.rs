//! A private trust store usable only for the configured account API origin.
use reqwest::{redirect::Policy, Certificate, Client, Method, Response};
use std::{error::Error, time::Duration};
use url::{Origin, Url};

pub(crate) type ApiResult<T> = Result<T, Box<dyn Error + Send + Sync>>;

pub(crate) struct ProxySettings {
    pub url: String,
    pub username: String,
    pub password: String,
}

pub(crate) struct ApiClient {
    origin: Origin,
    client: Client,
}

fn parse_target(value: &str) -> ApiResult<Url> {
    let url = Url::parse(value)?;
    if url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err("API URL must have a host and no credentials or fragment".into());
    }
    Ok(url)
}

fn parse_base(value: &str) -> ApiResult<Url> {
    let url = parse_target(value)?;
    if url.scheme() != "https" || url.query().is_some() {
        return Err("Account API base must use HTTPS and have no query".into());
    }
    Ok(url)
}

/// Other hosts retain the stock HTTP path; variants of our API host must not
/// fall through to its HTTP or raw TCP fallback paths.
pub(crate) fn classify_target(api_base: &str, target: &str) -> ApiResult<bool> {
    let base = parse_base(api_base)?;
    let target = parse_target(target)?;
    if base.origin() == target.origin() {
        return Ok(true);
    }
    if base.host() == target.host() {
        return Err("Account API scheme or port does not match the configured origin".into());
    }
    Ok(false)
}

impl ApiClient {
    pub(crate) fn new(
        api_base: &str,
        ca_pem: &[u8],
        proxy: Option<&ProxySettings>,
    ) -> ApiResult<Self> {
        let origin = parse_base(api_base)?.origin();
        let certificate = Certificate::from_pem(ca_pem)?;
        let mut builder = Client::builder()
            .use_rustls_tls()
            .tls_built_in_root_certs(false)
            .add_root_certificate(certificate)
            .https_only(true)
            .redirect(Policy::none())
            .no_proxy()
            .timeout(Duration::from_secs(12));
        if let Some(proxy) = proxy {
            let url = Url::parse(&proxy.url)?;
            if !matches!(url.scheme(), "http" | "socks5" | "socks5h") {
                return Err("Account API supports only HTTP CONNECT or SOCKS5 proxies; HTTPS proxies cannot use its private trust store".into());
            }
            if url.host_str().is_none() || !url.username().is_empty() || url.password().is_some() {
                return Err("Proxy URL must have a host; supply credentials separately".into());
            }
            let mut configured = reqwest::Proxy::all(url.as_str())?;
            if !proxy.username.is_empty() || !proxy.password.is_empty() {
                configured = configured.basic_auth(&proxy.username, &proxy.password);
            }
            builder = builder.proxy(configured);
        }
        Ok(Self {
            origin,
            client: builder.build()?,
        })
    }

    pub(crate) async fn request(
        &self,
        target: &str,
        method: Method,
        headers: Vec<(String, String)>,
        body: Option<&str>,
    ) -> ApiResult<Response> {
        let target = parse_target(target)?;
        if target.origin() != self.origin {
            return Err("Private API trust cannot be used for another origin".into());
        }
        let mut request = self.client.request(method, target);
        for (name, value) in headers {
            request = request.header(name, value);
        }
        if let Some(body) = body {
            request = request.body(body.to_owned());
        }
        Ok(request.send().await?)
    }
}
