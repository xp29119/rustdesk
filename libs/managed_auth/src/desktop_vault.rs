use crate::Vault;
use anyhow::Result;

/// Only construct in the authenticated system-service owner process.
/// Desktop UI processes must use IPC and must never read this vault directly.
pub struct ServiceVault;

/// Foreground login needs no administrator installation. Each OS user has a separate vault.
pub struct UserVault;

#[cfg(target_os = "macos")]
impl Vault for UserVault {
    fn load(&self) -> Result<Option<Vec<u8>>> {
        use security_framework::os::macos::keychain::SecKeychain;
        match SecKeychain::default()?.find_generic_password(SERVICE, ACCOUNT) {
            Ok((password, _)) => Ok(Some(password.to_vec())),
            Err(error) if error.code() == -25300 => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    fn save(&self, value: &[u8]) -> Result<()> {
        use security_framework::os::macos::keychain::SecKeychain;
        SecKeychain::default()?.set_generic_password(SERVICE, ACCOUNT, value)?;
        Ok(())
    }
}

#[cfg(target_os = "windows")]
impl Vault for UserVault {
    fn load(&self) -> Result<Option<Vec<u8>>> {
        ServiceVault.load()
    }
    fn save(&self, value: &[u8]) -> Result<()> {
        ServiceVault.save(value)
    }
}

const SERVICE: &str = "org.rustdesk.internal.auth.v1";
const ACCOUNT: &str = "8.138.230.183";

#[cfg(target_os = "macos")]
impl Vault for ServiceVault {
    fn load(&self) -> Result<Option<Vec<u8>>> {
        use security_framework::os::macos::keychain::SecKeychain;
        let keychain = SecKeychain::open("/Library/Keychains/System.keychain")?;
        match keychain.find_generic_password(SERVICE, ACCOUNT) {
            Ok((password, _)) => Ok(Some(password.to_vec())),
            Err(error) if error.code() == -25300 => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    fn save(&self, value: &[u8]) -> Result<()> {
        use security_framework::os::macos::keychain::SecKeychain;
        let keychain = SecKeychain::open("/Library/Keychains/System.keychain")?;
        keychain.set_generic_password(SERVICE, ACCOUNT, value)?;
        Ok(())
    }
}

#[cfg(target_os = "windows")]
impl Vault for ServiceVault {
    fn load(&self) -> Result<Option<Vec<u8>>> {
        use std::{io, ptr, slice};
        use windows_sys::Win32::{
            Foundation::{GetLastError, ERROR_NOT_FOUND},
            Security::Credentials::{CredFree, CredReadW, CRED_TYPE_GENERIC},
        };
        let target: Vec<u16> = format!("{SERVICE}/{ACCOUNT}\0").encode_utf16().collect();
        let mut credential = ptr::null_mut();
        // Windows isolates credentials by logon identity, including LocalSystem versus the user.
        unsafe {
            if CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut credential) == 0 {
                let error = GetLastError();
                return if error == ERROR_NOT_FOUND {
                    Ok(None)
                } else {
                    Err(io::Error::from_raw_os_error(error as i32).into())
                };
            }
            let value = if (*credential).CredentialBlobSize == 0 {
                Vec::new()
            } else {
                slice::from_raw_parts(
                    (*credential).CredentialBlob,
                    (*credential).CredentialBlobSize as usize,
                )
                .to_vec()
            };
            CredFree(credential.cast());
            Ok(Some(value))
        }
    }

    fn save(&self, value: &[u8]) -> Result<()> {
        use std::{io, mem};
        use windows_sys::Win32::Security::Credentials::{
            CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC,
        };
        anyhow::ensure!(value.len() <= 2560, "Protected login record is too large");
        let mut target: Vec<u16> = format!("{SERVICE}/{ACCOUNT}\0").encode_utf16().collect();
        let mut account: Vec<u16> = format!("{ACCOUNT}\0").encode_utf16().collect();
        let mut credential: CREDENTIALW = unsafe { mem::zeroed() };
        credential.Type = CRED_TYPE_GENERIC;
        credential.TargetName = target.as_mut_ptr();
        credential.UserName = account.as_mut_ptr();
        credential.CredentialBlobSize = value.len() as u32;
        credential.CredentialBlob = value.as_ptr() as *mut u8;
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE;
        if unsafe { CredWriteW(&credential, 0) } == 0 {
            return Err(io::Error::last_os_error().into());
        }
        Ok(())
    }
}
