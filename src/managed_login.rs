use hbb_common::{bail, tokio, ResultType};
use managed_auth::{Broker, Command, Response};
use std::sync::{Arc, OnceLock};

#[cfg(target_os = "android")]
mod android;

static BROKER: OnceLock<Result<Arc<Broker>, String>> = OnceLock::new();
type UiResult = Result<Response, String>;
type UiRequest = (Command, hbb_common::tokio::sync::oneshot::Sender<UiResult>);
static UI_REQUESTS: OnceLock<Result<hbb_common::tokio::sync::mpsc::Sender<UiRequest>, String>> =
    OnceLock::new();

/// FRB 1.x invokes synchronous Rust functions on its worker pool. This explicitly
/// starts one persistent async owner, so Android credentials and HTTP connections
/// outlive a UI call or a rendezvous attempt. It never nests an existing runtime.
pub fn start_ui_worker() -> ResultType<()> {
    let result = UI_REQUESTS.get_or_init(|| {
        let (tx, rx) = hbb_common::tokio::sync::mpsc::channel(16);
        std::thread::Builder::new()
            .name("managed-login-ui".into())
            .spawn(move || run_ui_worker(rx))
            .map_err(|error| error.to_string())?;
        Ok(tx)
    });
    result
        .as_ref()
        .map(|_| ())
        .map_err(|error| hbb_common::anyhow::anyhow!("{error}"))
}

#[tokio::main(flavor = "current_thread")]
async fn run_ui_worker(mut rx: hbb_common::tokio::sync::mpsc::Receiver<UiRequest>) {
    while let Some((command, reply)) = rx.recv().await {
        #[cfg(target_os = "android")]
        let result = execute_owned(command).await;
        #[cfg(not(target_os = "android"))]
        let result = execute(command).await;
        // Finish an already-started token rotation even if the requesting UI has closed.
        let _ = reply.send(result.map_err(|error| error.to_string()));
    }
}

fn ui_sender() -> ResultType<&'static hbb_common::tokio::sync::mpsc::Sender<UiRequest>> {
    UI_REQUESTS
        .get()
        .ok_or_else(|| hbb_common::anyhow::anyhow!("Login worker is not initialized"))?
        .as_ref()
        .map_err(|error| hbb_common::anyhow::anyhow!("{error}"))
}

pub fn request_from_ui(command: Command) -> ResultType<Response> {
    if hbb_common::tokio::runtime::Handle::try_current().is_ok() {
        bail!("Synchronous login bridge cannot run on an async worker");
    }
    let (reply, response) = hbb_common::tokio::sync::oneshot::channel();
    ui_sender()?.blocking_send((command, reply))?;
    response
        .blocking_recv()?
        .map_err(|error| hbb_common::anyhow::anyhow!("{error}"))
}

pub fn register_transport() {
    hbb_common::websocket::set_client_auth_provider_with_root(
        |url| {
            Box::pin(async move {
                match execute(Command::Headers { url }).await? {
                    Response::Headers {
                        authorization,
                        dpop,
                    } => Ok(vec![
                        ("Authorization".into(), authorization),
                        ("DPoP".into(), dpop),
                    ]),
                    _ => bail!("Managed login returned no transport authorization"),
                }
            })
        },
        crate::managed_config::TLS_ROOT_PEM,
    );
}

pub async fn execute(command: Command) -> ResultType<Response> {
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    return crate::ipc::managed_user::request(command).await;
    #[cfg(target_os = "android")]
    {
        let (reply, response) = hbb_common::tokio::sync::oneshot::channel();
        ui_sender()?.send((command, reply)).await?;
        return response
            .await?
            .map_err(|error| hbb_common::anyhow::anyhow!("{error}"));
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "android")))]
    bail!("Managed login is not supported on this platform");
}

pub async fn execute_owned(command: Command) -> ResultType<Response> {
    #[cfg(target_os = "windows")]
    if !crate::platform::windows::is_process_running_as_system(std::process::id())? {
        bail!("Only the installed system service may own managed credentials");
    }
    #[cfg(target_os = "macos")]
    if unsafe { hbb_common::libc::geteuid() } != 0 {
        bail!("Only the installed system service may own managed credentials");
    }
    #[cfg(any(target_os = "windows", target_os = "macos", target_os = "android"))]
    {
        let broker = BROKER.get_or_init(|| {
            #[cfg(any(target_os = "windows", target_os = "macos"))]
            let vault = Arc::new(managed_auth::desktop_vault::ServiceVault);
            #[cfg(target_os = "android")]
            let vault = Arc::new(android::AndroidVault);
            let broker = Arc::new(
                Broker::new_with_tls_root(
                    crate::managed_config::API_ORIGIN,
                    std::env::consts::OS,
                    vault,
                    crate::managed_config::TLS_ROOT_PEM,
                )
                .map_err(|error| error.to_string())?,
            );
            let background = broker.clone();
            hbb_common::tokio::spawn(async move {
                let mut timer =
                    hbb_common::tokio::time::interval(std::time::Duration::from_secs(60));
                loop {
                    timer.tick().await;
                    if let Err(error) = background.execute(Command::Status).await {
                        hbb_common::log::trace!("Managed login refresh unavailable: {error}");
                    }
                }
            });
            Ok(broker)
        });
        let broker = broker
            .as_ref()
            .map_err(|error| hbb_common::anyhow::anyhow!("{error}"))?;
        return broker.execute(command).await;
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "android")))]
    bail!("Managed login is not supported on this platform");
}

#[cfg(any(target_os = "windows", target_os = "macos"))]
pub async fn adopt_background(record: managed_auth::TransferRecord) -> ResultType<Response> {
    // This initializes and checks the privileged owner before accepting any transfer.
    let _ = execute_owned(Command::Status).await?;
    let broker = BROKER
        .get()
        .ok_or_else(|| hbb_common::anyhow::anyhow!("Service owner unavailable"))?
        .as_ref()
        .map_err(|e| hbb_common::anyhow::anyhow!("{e}"))?;
    broker.adopt_background_transfer(record).await
}
