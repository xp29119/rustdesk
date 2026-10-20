use super::{ConnectionTmpl, Data};
use hbb_common::{
    bail,
    tokio::io::{AsyncRead, AsyncWrite},
    ResultType,
};
use managed_auth::{Command, Response};

/// Verify the server end before transmitting passwords or requesting device proofs.
pub async fn request(command: Command) -> ResultType<Response> {
    let mut connection = super::connect_service(3_000)
        .await
        .map_err(|_| hbb_common::anyhow::anyhow!("请先安装并启动 RustDesk 后台服务"))?;
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
    connection.send(&Data::ManagedAuth(command)).await?;
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
    let Data::ManagedAuth(command) = data else {
        return false;
    };
    let result = crate::managed_login::execute_owned(command.clone())
        .await
        .map_err(|error| error.to_string());
    // The response type redacts proof/credential fields in its Debug implementation.
    if let Err(error) = connection.send(&Data::ManagedAuthResponse(result)).await {
        hbb_common::log::trace!("Managed service IPC response failed: {error}");
    }
    true
}
