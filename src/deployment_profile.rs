//! Reviewed public build profile using the upstream configuration maps.
use base::config::keys;
use hbb_common::config;

pub const MANAGED_UPDATES: bool = true;
const ID_SERVER: &str = env!("RD_PROFILE_ID_SERVER");
const RELAY_SERVER: &str = env!("RD_PROFILE_RELAY_SERVER");
pub(crate) const API_SERVER: &str = env!("RD_PROFILE_API_SERVER");
pub(crate) const API_CA_PEM: &[u8] = include_bytes!(env!("RD_PROFILE_CA_PEM_PATH"));
const SERVER_PUBLIC_KEY: &str = env!("RD_PROFILE_SERVER_PUBLIC_KEY");
const _: () = assert!(
    !ID_SERVER.is_empty()
        && !RELAY_SERVER.is_empty()
        && !API_SERVER.is_empty()
        && !API_CA_PEM.is_empty()
        && !SERVER_PUBLIC_KEY.is_empty()
);

pub fn apply() {
    config::OVERWRITE_SETTINGS.write().unwrap().extend([
        (keys::OPTION_CUSTOM_RENDEZVOUS_SERVER.into(), ID_SERVER.into()),
        (keys::OPTION_RELAY_SERVER.into(), RELAY_SERVER.into()),
        (keys::OPTION_API_SERVER.into(), API_SERVER.into()),
        (keys::OPTION_KEY.into(), SERVER_PUBLIC_KEY.into()),
        (keys::OPTION_ALLOW_AUTO_UPDATE.into(), "N".into()),
        (keys::OPTION_ALLOW_INSECURE_TLS_FALLBACK.into(), "N".into()),
    ]);
    config::OVERWRITE_LOCAL_SETTINGS
        .write()
        .unwrap()
        .insert(keys::OPTION_ENABLE_CHECK_UPDATE.into(), "N".into());
    config::OVERWRITE_LOCAL_SETTINGS
        .write()
        .unwrap()
        .insert(keys::OPTION_ENABLE_FLUTTER_HTTP_ON_RUST.into(), "Y".into());
    config::BUILTIN_SETTINGS.write().unwrap().extend([
        (keys::OPTION_HIDE_SERVER_SETTINGS.into(), "Y".into()),
        ("hide-web-console".into(), "Y".into()),
        (keys::OPTION_ALLOW_DEEP_LINK_SERVER_SETTINGS.into(), "N".into()),
        (keys::OPTION_ALLOW_HTTPS_21114.into(), "Y".into()),
    ]);
}
