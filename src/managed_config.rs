use base::config::keys;
use hbb_common::config;

pub const SERVER: &str = "wss://8.138.230.183/ws/id";
pub const RELAY: &str = "wss://8.138.230.183/ws/relay";
pub const API_ORIGIN: &str = "https://8.138.230.183";
pub const PUBLIC_KEY: &str = "qCRzeSq8oWC86sCeNAnJdLujx381mVtOfvP5g8nE0M8=";
pub const DISABLE_UPDATES: &str = "disable-client-updates";

pub const fn enabled() -> bool {
    cfg!(feature = "managed-client")
}

pub fn init() {
    if !enabled() {
        return;
    }
    #[cfg(feature = "managed-client")]
    crate::managed_login::register_transport();
    config::OVERWRITE_SETTINGS.write().unwrap().extend([
        (keys::OPTION_CUSTOM_RENDEZVOUS_SERVER.into(), SERVER.into()),
        (keys::OPTION_RELAY_SERVER.into(), RELAY.into()),
        (keys::OPTION_KEY.into(), PUBLIC_KEY.into()),
        (keys::OPTION_API_SERVER.into(), String::new()),
        (keys::OPTION_ALLOW_AUTO_UPDATE.into(), "N".into()),
        (keys::OPTION_ALLOW_WEBSOCKET.into(), "Y".into()),
    ]);
    config::OVERWRITE_LOCAL_SETTINGS
        .write()
        .unwrap()
        .insert(keys::OPTION_ENABLE_CHECK_UPDATE.into(), "N".into());
    config::BUILTIN_SETTINGS.write().unwrap().extend([
        ("hide-server-settings".into(), "Y".into()),
        (DISABLE_UPDATES.into(), "Y".into()),
        ("managed-login".into(), "Y".into()),
    ]);
}

#[cfg(test)]
mod tests {
    use super::*;
    use hbb_common::config::{Config, LocalConfig};

    #[test]
    #[cfg(feature = "managed-client")]
    fn managed_server_and_update_policy_cannot_be_overridden() {
        init();
        Config::set_option(
            keys::OPTION_CUSTOM_RENDEZVOUS_SERVER.into(),
            "other.example".into(),
        );
        Config::set_option(keys::OPTION_ALLOW_AUTO_UPDATE.into(), "Y".into());
        LocalConfig::set_option(keys::OPTION_ENABLE_CHECK_UPDATE.into(), "Y".into());
        assert_eq!(Config::get_rendezvous_server(), SERVER);
        assert!(config::use_ws());
        assert_eq!(Config::get_option(keys::OPTION_RELAY_SERVER), RELAY);
        assert_eq!(Config::get_option(keys::OPTION_KEY), PUBLIC_KEY);
        assert!(!Config::get_bool_option(keys::OPTION_ALLOW_AUTO_UPDATE));
        assert_eq!(LocalConfig::get_option(keys::OPTION_ENABLE_CHECK_UPDATE), "N");
    }
}
