//! A per-user credential owner, started on demand without an administrator installation.
//! It is not an OS startup service and exits after two minutes without requests.
use super::{ConnClient, Connection, ConnectionTmpl, Data};
use hbb_common::{anyhow, bail, config::Config, futures::StreamExt, tokio, ResultType};
use managed_auth::{desktop_vault::UserVault, Broker, Command, Response};
use std::{sync::Arc, time::Duration};

fn privileged() -> ResultType<bool> {
    #[cfg(target_os = "macos")]
    return Ok(unsafe { hbb_common::libc::geteuid() } == 0);
    #[cfg(target_os = "windows")]
    return crate::platform::windows::is_process_running_as_system(std::process::id());
}

#[cfg(target_os = "windows")]
fn process_user(pid: u32) -> ResultType<Vec<u8>> {
    use windows::Win32::{
        Foundation::{CloseHandle, HANDLE},
        Security::{
            GetLengthSid, GetTokenInformation, IsValidSid, TokenUser, TOKEN_QUERY, TOKEN_USER,
        },
        System::Threading::{OpenProcess, OpenProcessToken, PROCESS_QUERY_LIMITED_INFORMATION},
    };
    unsafe {
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid)?;
        let mut token = HANDLE::default();
        let result = (|| -> ResultType<Vec<u8>> {
            OpenProcessToken(process, TOKEN_QUERY, &mut token)?;
            let mut size = 0;
            let _ = GetTokenInformation(token, TokenUser, None, 0, &mut size);
            if size < std::mem::size_of::<TOKEN_USER>() as u32 || size > 65536 {
                bail!("Invalid process identity");
            }
            let mut buffer = vec![0u8; size as usize];
            GetTokenInformation(
                token,
                TokenUser,
                Some(buffer.as_mut_ptr().cast()),
                size,
                &mut size,
            )?;
            let user = std::ptr::read_unaligned(buffer.as_ptr().cast::<TOKEN_USER>());
            if !IsValidSid(user.User.Sid).as_bool() {
                bail!("Invalid process SID");
            }
            let length = GetLengthSid(user.User.Sid) as usize;
            if length == 0 || length > 68 {
                bail!("Invalid process SID length");
            }
            Ok(std::slice::from_raw_parts(user.User.Sid.0.cast::<u8>(), length).to_vec())
        })();
        if !token.is_invalid() {
            let _ = CloseHandle(token);
        }
        let _ = CloseHandle(process);
        result
    }
}

fn postfix() -> ResultType<String> {
    #[cfg(target_os = "macos")]
    return Ok("_managed_user".into());
    #[cfg(target_os = "windows")]
    return Ok(format!(
        "_managed_user_{}",
        process_user(std::process::id())?
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    ));
}

struct OwnerLock {
    #[cfg(target_os = "macos")]
    _file: std::fs::File,
    #[cfg(target_os = "windows")]
    handle: windows::Win32::Foundation::HANDLE,
}

#[cfg(target_os = "windows")]
impl Drop for OwnerLock {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.handle);
        }
    }
}

fn owner_lock() -> ResultType<Option<OwnerLock>> {
    #[cfg(target_os = "macos")]
    {
        use hbb_common::libc;
        use std::os::{
            fd::AsRawFd,
            unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
        };
        // Use the same UID-scoped namespace as the socket, independent of HOME overrides.
        let socket_path = std::path::PathBuf::from(Config::ipc_path(&postfix()?));
        let folder = socket_path
            .parent()
            .ok_or_else(|| anyhow::anyhow!("Invalid account IPC path"))?;
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&folder)?;
        let info = std::fs::symlink_metadata(&folder)?;
        if !info.is_dir() || info.uid() != unsafe { libc::geteuid() } || info.mode() & 0o077 != 0 {
            bail!("Invalid credential-owner directory");
        }
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(folder.join("managed-owner.lock"))?;
        let info = file.metadata()?;
        if !info.is_file()
            || info.uid() != unsafe { libc::geteuid() }
            || info.nlink() != 1
            || info.mode() & 0o077 != 0
        {
            bail!("Invalid credential-owner lock");
        }
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::WouldBlock {
                return Ok(None);
            }
            return Err(error.into());
        }
        Ok(Some(OwnerLock { _file: file }))
    }
    #[cfg(target_os = "windows")]
    {
        use windows::{
            core::PCWSTR,
            Win32::{
                Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS},
                System::Threading::CreateMutexW,
            },
        };
        let name: Vec<u16> = format!("Global\\RustDesk{}\0", postfix()?)
            .encode_utf16()
            .collect();
        unsafe {
            let handle = CreateMutexW(None, true, PCWSTR(name.as_ptr()))?;
            if GetLastError() == ERROR_ALREADY_EXISTS {
                let _ = CloseHandle(handle);
                return Ok(None);
            }
            Ok(Some(OwnerLock { handle }))
        }
    }
}

fn verify_server(connection: &ConnectionTmpl<ConnClient>, suffix: &str) -> ResultType<()> {
    #[cfg(target_os = "macos")]
    {
        if connection.peer_uid() != Some(unsafe { hbb_common::libc::geteuid() }) {
            bail!("Credential owner belongs to another user");
        }
        super::ipc_auth::ensure_peer_executable_matches_current_by_pid_opt(
            connection.peer_pid(),
            suffix,
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
        if process_user(pid)? != process_user(std::process::id())? {
            bail!("Credential owner belongs to another user");
        }
        super::ipc_auth::ensure_peer_executable_matches_current_by_pid_opt(Some(pid), suffix)?;
    }
    Ok(())
}

fn verify_client(connection: &Connection, suffix: &str) -> ResultType<()> {
    #[cfg(target_os = "macos")]
    {
        if connection.peer_uid() != Some(unsafe { hbb_common::libc::geteuid() }) {
            bail!("Client belongs to another user");
        }
        super::ipc_auth::ensure_peer_executable_matches_current_by_pid_opt(
            connection.peer_pid(),
            suffix,
        )?;
    }
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::io::AsRawHandle;
        use windows::Win32::{Foundation::HANDLE, System::Pipes::GetNamedPipeClientProcessId};
        let mut pid = 0;
        unsafe {
            GetNamedPipeClientProcessId(
                HANDLE(connection.inner.get_ref().as_raw_handle()),
                &mut pid,
            )?;
        }
        if process_user(pid)? != process_user(std::process::id())? {
            bail!("Client belongs to another user");
        }
        super::ipc_auth::ensure_peer_executable_matches_current_by_pid_opt(Some(pid), suffix)?;
    }
    Ok(())
}

pub async fn request(command: Command) -> ResultType<Response> {
    if privileged()? {
        return super::managed::request(command).await;
    }
    let suffix = postfix()?;
    let mut connection = match super::connect(500, &suffix).await {
        Ok(connection) => connection,
        Err(_) => {
            let child = tokio::task::spawn_blocking(|| crate::run_me(vec!["--managed-auth-agent"]))
                .await??;
            std::thread::Builder::new()
                .name("account-owner-reaper".into())
                .spawn(move || {
                    let mut child = child;
                    if let Err(error) = child.wait() {
                        hbb_common::log::trace!(
                            "Account component exit could not be collected: {error}"
                        );
                    }
                })?;
            let mut ready = None;
            for _ in 0..30 {
                if let Ok(connection) = super::connect(100, &suffix).await {
                    ready = Some(connection);
                    break;
                }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
            ready.ok_or_else(|| anyhow::anyhow!("账号组件正在启动，请稍后重试"))?
        }
    };
    verify_server(&connection, &suffix)?;
    connection.send(&Data::ManagedAuth(command)).await?;
    match connection.next_timeout(55_000).await? {
        Some(Data::ManagedAuthResponse(Ok(response))) => Ok(response),
        Some(Data::ManagedAuthResponse(Err(error))) => bail!("{error}"),
        _ => bail!("账号组件暂时不可用"),
    }
}

async fn route(broker: &Broker, command: Command) -> ResultType<Response> {
    let local = broker.local_status().await?;
    match super::managed::request(Command::Status).await {
        Ok(Response::Status(service)) => {
            // Older installed services can still serve their existing login, but must
            // explicitly support adoption before a foreground credential is frozen.
            let transferable = match super::managed::supports_transfer().await {
                Ok(supported) => supported,
                Err(error) => {
                    hbb_common::log::trace!("Background transfer is not available: {error}");
                    false
                }
            };
            if !transferable {
                if !service.username.is_empty()
                    && (local.username.is_empty()
                        || (local.state == "background" && local.device_id == service.device_id))
                {
                    return super::managed::request(command).await;
                }
                return broker.execute(command).await;
            }
            if !local.username.is_empty()
                && !service.username.is_empty()
                && local.device_id != service.device_id
            {
                // A different machine-wide account must not replace a user's foreground login.
                return broker.execute(command).await;
            }
            if !local.username.is_empty() {
                if let Some(record) = broker.prepare_background_transfer().await? {
                    let response = super::managed::transfer(record).await?;
                    let Response::Status(adopted) = response else {
                        bail!("Invalid service acknowledgement");
                    };
                    broker
                        .finish_background_transfer(&adopted.device_id)
                        .await?;
                }
                return super::managed::request(command).await;
            }
            if !service.username.is_empty() {
                return super::managed::request(command).await;
            }
            // A first login creates the identity in the user vault, even when the optional
            // service is present. Enabling background access keeps that device approval.
            let response = broker.execute(command).await?;
            if let Some(record) = broker.prepare_background_transfer().await? {
                let adopted = super::managed::transfer(record).await?;
                let Response::Status(adopted) = adopted else {
                    bail!("Invalid service acknowledgement");
                };
                broker
                    .finish_background_transfer(&adopted.device_id)
                    .await?;
                return super::managed::request(Command::Status).await;
            }
            Ok(response)
        }
        Ok(_) => bail!("Invalid background service response"),
        Err(error) => {
            if local.state == "background" && !matches!(&command, Command::Login { .. }) {
                return Err(error);
            }
            // An absent or unverifiable optional service receives no credentials;
            // ordinary foreground login remains available in the current user's vault.
            broker.execute(command).await
        }
    }
}

/// Explicit CLI entry point; no runtime is created inside an existing async operation.
#[tokio::main(flavor = "current_thread")]
pub async fn run_agent() -> ResultType<()> {
    if privileged()? {
        bail!("Foreground credential owner must run as the ordinary user");
    }
    let Some(_lock) = owner_lock()? else {
        return Ok(());
    };
    let suffix = postfix()?;
    let mut incoming = super::new_listener(&suffix).await?;
    let broker = Broker::new_with_tls_root(
        crate::managed_config::API_ORIGIN,
        std::env::consts::OS,
        Arc::new(UserVault),
        crate::managed_config::TLS_ROOT_PEM,
    )?;
    // One request at a time also serializes transitions between user and system ownership.
    while let Ok(Some(accepted)) =
        tokio::time::timeout(Duration::from_secs(120), incoming.next()).await
    {
        let Ok(stream) = accepted else {
            continue;
        };
        let mut connection = Connection::new(stream);
        if verify_client(&connection, &suffix).is_err() {
            continue;
        }
        if let Ok(Some(Data::ManagedAuth(command))) = connection.next_timeout(3_000).await {
            let result = route(&broker, command).await.map_err(|e| e.to_string());
            if let Err(error) = connection.send(&Data::ManagedAuthResponse(result)).await {
                hbb_common::log::trace!("Account component response unavailable: {error}");
            }
        }
    }
    Ok(())
}
