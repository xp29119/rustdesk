#!/usr/bin/env bash
set -euo pipefail

# ripgrep anchors to verify after upstream sync

root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

echo "[1] Rust constants & functions"
rg -n "RENDEZVOUS_SERVERS|RS_PUB_KEY|DEFAULT_PERMANENT_PASSWORD|fn set_permanent_password|fn get_permanent_password" libs/hbb_common/src/config.rs || true

echo "[2] Rust client defaults injection"
rg -n "fn get_api_server_|fn load_custom_client|custom-rendezvous-server|relay-server|api-server|key|enable-check-update|allow-auto-update|verification-method|approve-mode|hide-server-settings" src/common.rs || true

echo "[3] IPC password path"
rg -n "get_permanent_password\(|Config::get_permanent_password" src/ipc.rs || true

echo "[4] i18n keys (CN/EN)"
rg -n "login_required_hint_under_input|login_required_dialog_title2|login_required_dialog_body2|go_to_login|login_dialog_footer_note" src/lang/cn.rs src/lang/en.rs || true

echo "[5] Desktop avatar & settings navigation"
rg -n "Icons.person|DesktopSettingPage.switch2page\(SettingsTabKey.account\)|loginDialog\(|canBeBlocked\(" flutter/lib/desktop/pages/desktop_tab_page.dart || true

echo "[6] Login hint under input"
rg -n "login_required_hint_under_input|titleLarge\?\.color|withOpacity\(|marginOnly\(top: 10\)" flutter/lib/desktop/pages/connection_page.dart || true

echo "[7] Pre-check login & dialog (width=420)"
rg -n "connect\(BuildContext|showLoginRequiredDialog\(|contentBoxConstraints: BoxConstraints\(minWidth: 420\)|dialogButton\(translate\('go_to_login'\)|dialogButton\(translate\('Cancel'\)" flutter/lib/common.dart || true

echo "[8] Fallback server error dialog (width=420)"
rg -n "Connection failed, please login!|login_required_dialog_title2|login_required_dialog_body2|contentBoxConstraints: BoxConstraints\(minWidth: 420\)|_loginPromptShown" flutter/lib/desktop/pages/desktop_home_page.dart || true

echo "[9] Login dialog footer note"
rg -n "login_dialog_footer_note|withOpacity\(0.5\)|EdgeInsets.only\(top: 10\)" flutter/lib/common/widgets/login.dart || true

echo "[10] Mobile & Sciter entry hiding"
rg -n "ScanButton|scan_page\.dart|ServerConfigImportExportWidgets\(|#custom-server" -S flutter/lib/mobile/pages/settings_page.dart flutter/lib/common/widgets/setting_widgets.dart src/ui/index.tis || true

echo "Done."

