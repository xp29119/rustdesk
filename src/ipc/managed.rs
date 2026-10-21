use super::{ConnClient, ConnectionTmpl, Data};
use hbb_common::{
    bail,
    tokio::io::{AsyncRead, AsyncWrite},
    ResultType,
};
use managed_auth::{Command, Response, TransferRecord};

#[derive(Debug)]
pub struct UntrustedService;
impl std::fmt::Display for UntrustedService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("后台服务身份校验失败")
    }
}
impl std::error::Error for UntrustedService {}

/// Verify the server end before transmitting passwords or requesting device proofs.
async fn connect_root() -> ResultType<ConnectionTmpl<ConnClient>> {
    let connection = super::connect_service(3_000)
        .await
        .map_err(|_| hbb_common::anyhow::anyhow!("无人值守服务暂不可用"))?;
    let verified = (|| -> ResultType<()> {
        #[cfg(target_os = "macos")]
        {
            if connection.peer_uid() != Some(0) {
                bail!("The managed credential service is not privileged");
            }
            super::ipc_auth::ensure_peer_executable_matches_current_by_pid_opt(
                connection.peer_pid(),
                crate::POSTFIX_SERVICE,
            )?;
        }
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::io::AsRawHandle;
            use windows::Win32::{Foundation::HANDLE, System::Pipes::GetNamedPipeServerProcessId};
            let mut pid = 0;
            unsafe {
                GetNamedPipeServerProcessId(
                    HANDLE(connection.inner.get_ref().as_raw_handle()),
                    &mut pid,
                )?;
            }
            if !crate::platform::windows::is_process_running_as_system(pid)? {
                bail!("The managed credential service is not LocalSystem");
            }
            super::ensure_peer_executable_matches_current_by_pid_opt(
                Some(pid),
                crate::POSTFIX_SERVICE,
            )?;
        }
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        bail!("Managed service IPC is not supported on this platform");
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        Ok(())
    })();
    verified.map_err(|_| hbb_common::anyhow::anyhow!(UntrustedService))?;
    Ok(connection)
}

pub async fn request(command: Command) -> ResultType<Response> {
    let mut connection = connect_root().await?;
    connection.send(&Data::ManagedAuth(command)).await?;
    response(&mut connection).await
}

pub async fn transfer(record: TransferRecord) -> ResultType<Response> {
    let mut connection = connect_root().await?;
    connection.send(&Data::ManagedTransfer(record)).await?;
    response(&mut connection).await
}

pub async fn supports_transfer() -> ResultType<bool> {
    let mut connection = connect_root().await?;
    connection.send(&Data::ManagedAuthCapabilities).await?;
    Ok(matches!(
        connection.next_timeout(3_000).await,
        Ok(Some(Data::ManagedAuthCapabilitiesResponse(1)))
    ))
}

async fn response(connection: &mut ConnectionTmpl<ConnClient>) -> ResultType<Response> {
    match connection.next_timeout(25_000).await? {
        Some(Data::ManagedAuthResponse(Ok(response))) => Ok(response),
        Some(Data::ManagedAuthResponse(Err(error))) => bail!("{error}"),
        _ => bail!("Managed credential service did not respond"),
    }
}

/// Called only after the existing protected `_service` peer/executable authorization.
pub async fn handle<T>(data: &Data, connection: &mut ConnectionTmpl<T>) -> bool
where
    T: AsyncRead + AsyncWrite + Unpin,
{
    if matches!(data, Data::ManagedAuthCapabilities) {
        if let Err(error) = connection
            .send(&Data::ManagedAuthCapabilitiesResponse(1))
            .await
        {
            hbb_common::log::trace!("Credential service capability reply failed: {error}");
        }
        return true;
    }
    let result = match data {
        Data::ManagedAuth(command) => crate::managed_login::execute_owned(command.clone()).await,
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        Data::ManagedTransfer(record) => {
            crate::managed_login::adopt_background(record.clone()).await
        }
        _ => return false,
    }
    .map_err(|error| error.to_string());
    // The response type redacts proof/credential fields in its Debug implementation.
    if let Err(error) = connection.send(&Data::ManagedAuthResponse(result)).await {
        hbb_common::log::trace!("Managed service IPC response failed: {error}");
    }
    true
}
