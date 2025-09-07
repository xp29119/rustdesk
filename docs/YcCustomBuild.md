## YC 内部定制改造清单（可直接用于复刻 | 含精确锚点与代码片段）

说明：当你同步上游后，本文件用于“一次性复刻所有改造”。逐条按本清单执行即可，不需要重新描述业务需求。

范围：
- 平台：本轮主要是 Windows/macOS（Flutter 桌面）。移动端仅移除服务器导入相关入口。
- 共享层：服务器地址与密码策略在 Rust 公共层（影响四端）。

---

### 0. 固定值（用于校验）
- Rendezvous 域名：`yc.xinsikeji.com`
- Relay 域名：`yc.xinsikeji.com`
- Rendezvous 端口：`21116`
- Relay 端口：`21117`
- API 默认兜底：`http://yc.xinsikeji.com:21114`
- Key（RS 公钥）：`4fgpDL4LxpKBTNNbItHzGy1PAYNTH36uNF8cHmXKkZk=`
- 默认永久密码：`ykgxZu9TmU4169GErxpr`

---

### 1) 服务器默认与隐藏（Rust 层，四端共享）

文件：`libs/hbb_common/src/config.rs`
- 常量：
  - `RENDEZVOUS_SERVERS = ["yc.xinsikeji.com"]`
  - `RS_PUB_KEY = "4fgpDL4LxpKBTNNbItHzGy1PAYNTH36uNF8cHmXKkZk="`
- 默认永久密码：
  - `const DEFAULT_PERMANENT_PASSWORD: &str = "ykgxZu9TmU4169GErxpr";`
- 永久密码逻辑：
  - `set_permanent_password()`：允许保存用户自定义（不要“硬禁止覆盖”的逻辑）。
  - `get_permanent_password()`：优先返回用户保存；若为空，返回 `DEFAULT_PERMANENT_PASSWORD`。

检索锚点：
```
RENDEZVOUS_SERVERS
RS_PUB_KEY
DEFAULT_PERMANENT_PASSWORD
fn set_permanent_password
fn get_permanent_password
```

建议片段（仅示意，按真实函数体就地替换关键行）：
```
pub const RENDEZVOUS_SERVERS: &[&str] = &["yc.xinsikeji.com"]; // 原为 rs-*.rustdesk.com
pub const RS_PUB_KEY: &str = "4fgpDL4LxpKBTNNbItHzGy1PAYNTH36uNF8cHmXKkZk=";
pub const DEFAULT_PERMANENT_PASSWORD: &str = "ykgxZu9TmU4169GErxpr";

pub fn set_permanent_password(pass: &str) { /* 允许用户写入覆盖 */ }
pub fn get_permanent_password() -> String { /* 优先返回用户保存，为空回落 DEFAULT_PERMANENT_PASSWORD */ }
```

文件：`src/common.rs`
- 函数 `get_api_server_()`：
  - 默认兜底从 `https://admin.rustdesk.com` 改为 `http://yc.xinsikeji.com:21114`。
- 函数 `load_custom_client()`：启动注入（不覆盖用户已有设置）：
  - 写入 `DEFAULT_SETTINGS`：
    - `custom-rendezvous-server = "yc.xinsikeji.com:21116"`
    - `relay-server = "yc.xinsikeji.com:21117"`
    - `api-server = "http://yc.xinsikeji.com:21114"`
    - `key = "4fgpDL4LxpKBTNNbItHzGy1PAYNTH36uNF8cHmXKkZk="`
    - `enable-check-update = "N"`
    - `allow-auto-update = "N"`
    - `verification-method = "use-both-passwords"`
    - `approve-mode = "password"`
  - 写入 `BUILTIN_SETTINGS`：
    - `hide-server-settings = "Y"`（隐藏服务器设置入口）
  - “种子”默认永久密码（仅初次）：
    - 若 `CONFIG.password` 为空 → `Config::set_permanent_password(DEFAULT_PERMANENT_PASSWORD)`。

检索锚点：
```
fn get_api_server_
fn load_custom_client
DEFAULT_SETTINGS.insert("custom-rendezvous-server"
BUILTIN_SETTINGS.insert("hide-server-settings"
```

建议片段（仅示意，按真实 Map/HashMap 写入位置添加或修改）：
```
DEFAULT_SETTINGS.insert("custom-rendezvous-server".into(), "yc.xinsikeji.com:21116".into());
DEFAULT_SETTINGS.insert("relay-server".into(), "yc.xinsikeji.com:21117".into());
DEFAULT_SETTINGS.insert("api-server".into(), "http://yc.xinsikeji.com:21114".into());
DEFAULT_SETTINGS.insert("key".into(), "4fgpDL4LxpKBTNNbItHzGy1PAYNTH36uNF8cHmXKkZk=".into());
DEFAULT_SETTINGS.insert("enable-check-update".into(), "N".into());
DEFAULT_SETTINGS.insert("allow-auto-update".into(), "N".into());
DEFAULT_SETTINGS.insert("verification-method".into(), "use-both-passwords".into());
DEFAULT_SETTINGS.insert("approve-mode".into(), "password".into());

BUILTIN_SETTINGS.insert("hide-server-settings".into(), "Y".into());
```

文件：`src/ipc.rs`
- `get_permanent_password()`：直接调用 `Config::get_permanent_password()`，避免被 IPC 缓存覆盖。

检索锚点：
```
fn get_permanent_password
Config::get_permanent_password
```

老桌面 Sciter（如仍使用）：
- 文件：`src/ui/index.tis`：注释/移除 `<li #custom-server>`。

---

### 2) 移动端入口隐藏（Flutter）

文件：`flutter/lib/mobile/pages/settings_page.dart`
- 移除 `ScanButton` 及 `scan_page.dart` 的 import（关闭扫码导入服务器）。

文件：`flutter/lib/common/widgets/setting_widgets.dart`
- 函数 `ServerConfigImportExportWidgets()` 返回空列表（隐藏“导入/导出服务器配置”）。

---

### 3) 桌面端 UX（Windows/macOS）

顶部头像入口（登录/账号页直达）：
- 文件：`flutter/lib/desktop/pages/desktop_tab_page.dart`
  - 右上角新增头像 `Icon(Icons.person)`。
  - 颜色：登录 = 蓝色高亮；未登录 = 弱化灰/白（与原菜单一致）。
  - 点击：
    - 未登录 → `loginDialog()`
    - 已登录 → `DesktopSettingPage.switch2page(SettingsTabKey.account)`（无论设置是否已开，均强切至“账号”页）。

检索锚点：
```
Icons.person
DesktopSettingPage.switch2page(SettingsTabKey.account)
loginDialog(
```

首页输入框下未登录提示：
- 文件：`flutter/lib/desktop/pages/connection_page.dart`
  - i18n key：`login_required_hint_under_input`
  - 最终中文文案：`登录后才能控制其他设备`
  - 样式：`fontSize:14`；颜色随主题自适配（亮色更深、暗色更亮）；上边距 `10`。

检索锚点：
```
login_required_hint_under_input
Text(translate('login_required_hint_under_input')
Theme.of(context).textTheme.titleLarge?.color
```

连接前置拦截 + 统一“需要登录”对话框：
- 文件：`flutter/lib/common.dart`
  - 在 `connect(...)` 顶部：
    - 条件：`isDesktop && (isWindows || isMacOS) && !gFFI.userModel.isLogin`
    - 行为：`await showLoginRequiredDialog(context); return;`
  - 新增 `showLoginRequiredDialog(BuildContext context)`：
    - 标题键：`login_required_dialog_title2` → `需要登录`
    - 正文键：`login_required_dialog_body2` → `未登录，无法控制其他设备。`
    - 按钮：
      - `Cancel`（复用全局“取消”，描边按钮）
      - `go_to_login` → `去登录`（主按钮，蓝色）

检索锚点：
```
connect(BuildContext context,
showLoginRequiredDialog(BuildContext context)
dialogButton(translate('go_to_login')
dialogButton(translate('Cancel')
```

服务端错误兜底拦截（保留）：
- 文件：`flutter/lib/desktop/pages/desktop_home_page.dart`
  - 若错误字符串为 `Connection failed, please login!` 且未登录 → 弹与上面相同文案/按钮的对话框，含防抖标志避免重复弹。

检索锚点：
```
"Connection failed, please login!"
login_required_dialog_title2
login_required_dialog_body2
_loginPromptShown
```

登录弹窗底部说明：
- 文件：`flutter/lib/common/widgets/login.dart`
  - i18n key：`login_dialog_footer_note`
  - 中文：`账号由管理员分配，暂不支持注册。`
  - 样式：`fontSize:14`，颜色取 `titleLarge?.color` 的弱化；桌面端可见。

检索锚点：
```
login_dialog_footer_note
Text(translate('login_dialog_footer_note')
```

---

### 4) i18n 键值与最终文案

文件：`src/lang/cn.rs`（中文，最终版）
- `login_required_hint_under_input = "登录后才能控制其他设备"`
- `login_required_dialog_title2 = "需要登录"`
- `login_required_dialog_body2 = "未登录，无法控制其他设备。"`
- `go_to_login = "去登录"`
- `login_dialog_footer_note = "账号由管理员分配，暂不支持注册。"`
- 旧键（当前未用，保留兼容）：
  - `login_required_dialog_title = "连接失败"`
  - `login_required_dialog_body = "未登录状态无法控制远程设备，请先登录后再试。"`

文件：`src/lang/en.rs`（英文）
- `login_required_hint_under_input = "Login required to start remote control"`
- `login_required_dialog_title2 = "Login required"`
- `login_required_dialog_body2 = "Not logged in, unable to initiate remote control."`
- `go_to_login = "Go to login"`
- `login_dialog_footer_note = "Account assigned by administrator, registration is not supported."`

---

### 5) 更新机制（私有版默认关闭）
- 位置：`src/common.rs / load_custom_client()`
  - `enable-check-update = "N"`
  - `allow-auto-update = "N"`
  - 目的：不连公共更新服务器；上游逻辑保留，可按需改回。

---

### 6) 快速复刻顺序（同步上游后按序执行）
1. `libs/hbb_common/src/config.rs`：恢复 RENDEZVOUS_SERVERS、RS_PUB_KEY、DEFAULT_PERMANENT_PASSWORD 与 `set/get_permanent_password()` 逻辑。
2. `src/common.rs`：`get_api_server_()` 默认兜底；`load_custom_client()` 注入（四项服务器默认、关闭更新、认证模式、隐藏入口、默认密码“种子”）。
3. `src/ipc.rs`：`get_permanent_password()` 直连 `Config::get_permanent_password()`。
4. UI 隐藏：`src/ui/index.tis` 注释服务器入口；Flutter 移动端移除扫码与导入/导出。
5. 桌面 UX：头像 → 账号页；输入框提示；`connect()` 前置拦截 + 统一对话框；错误兜底对话框；登录弹窗底部说明。
6. i18n：核对并写入上述键值与最终文案（至少中文）。

---

### 7) 变更检索锚点（便于全局搜）
- 服务器：`custom-rendezvous-server|relay-server|api-server|key|load_custom_client|get_api_server_`
- 密码：`DEFAULT_PERMANENT_PASSWORD|get_permanent_password|set_permanent_password|verification-method|approve-mode`
- 桌面 UX：`connect\(|showLoginRequiredDialog\(|Connection failed, please login!|SettingsTabKey.account|DesktopSettingPage.switch2page`
- UI 隐藏：`ServerConfigImportExportWidgets|ScanButton|scan_page|#custom-server`

---

### 8) 版本与回滚（历史）
- Tag：`v1.4.2-yc-internal-20250907`
- 回滚到该版本并强推 master：
  - `git reset --hard v1.4.2-yc-internal-20250907 && git push -f origin master`

---

附注：如需把某些值（例如 API）通过编译期环境变量覆盖，仍可使用 `API_SERVER=... cargo build` 方式，上述默认仅作为兜底，不影响上游优先级链与行为一致性。


