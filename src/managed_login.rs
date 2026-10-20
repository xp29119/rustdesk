use hbb_common::{bail, ResultType};
use managed_auth::{Broker, Command, Response};
use std::sync::{Arc, OnceLock};

#[cfg(target_os = "android")]
mod android;

static BROKER: OnceLock<Result<Arc<Broker>, String>> = OnceLock::new();

pub fn register_transport() {
    hbb_common::websocket::set_client_auth_provider(|url| {
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
    });
}

pub async fn execute(command: Command) -> ResultType<Response> {
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    return crate::ipc::managed::request(command).await;
    #[cfg(target_os = "android")]
    return execute_owned(command).await;
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
                Broker::new(
                    crate::managed_config::API_ORIGIN,
                    std::env::consts::OS,
                    vault,
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
