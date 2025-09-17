## YC 内部定制复刻（单文件版）

用途：这是“单文件全集”，包含复刻说明 + 关键常量 + 关键文件清单 + 检查脚本 + 上下文代码片段。把本文件单独保存即可，未来直接发我照此逐项复刻。

---

### A. 固定值（必须一致）

```env
RENDEZVOUS_DOMAIN=yc.xinsikeji.com
RELAY_DOMAIN=yc.xinsikeji.com
RENDEZVOUS_PORT=21116
RELAY_PORT=21117
API_DEFAULT=http://yc.xinsikeji.com:21114
RS_PUB_KEY=4fgpDL4LxpKBTNNbItHzGy1PAYNTH36uNF8cHmXKkZk=
DEFAULT_PERMANENT_PASSWORD=ykgxZu9TmU4169GErxpr
VERIFICATION_METHOD=use-both-passwords
APPROVE_MODE=password
ENABLE_CHECK_UPDATE=N
ALLOW_AUTO_UPDATE=N
```

---

### B. 涉及文件清单（绝对路径示例，换机后按项目根相对路径对应）

```
/Users/huangfubin/rustdesk/libs/hbb_common/src/config.rs
/Users/huangfubin/rustdesk/src/common.rs
/Users/huangfubin/rustdesk/src/ipc.rs
/Users/huangfubin/rustdesk/src/lang/cn.rs
/Users/huangfubin/rustdesk/src/lang/en.rs
/Users/huangfubin/rustdesk/src/ui/index.tis
/Users/huangfubin/rustdesk/flutter/lib/common.dart
/Users/huangfubin/rustdesk/flutter/lib/desktop/pages/desktop_tab_page.dart
/Users/huangfubin/rustdesk/flutter/lib/desktop/pages/connection_page.dart
/Users/huangfubin/rustdesk/flutter/lib/desktop/pages/desktop_home_page.dart
/Users/huangfubin/rustdesk/flutter/lib/common/widgets/login.dart
/Users/huangfubin/rustdesk/flutter/lib/mobile/pages/settings_page.dart
/Users/huangfubin/rustdesk/flutter/lib/common/widgets/setting_widgets.dart
```

---

### C. 复刻顺序（不看行号，只用语义锚点）

1) `libs/hbb_common/src/config.rs`
- 常量：
```rust
pub const RENDEZVOUS_SERVERS: &[&str] = &["yc.xinsikeji.com"]; // 原为 rs-*.rustdesk.com
pub const RS_PUB_KEY: &str = "4fgpDL4LxpKBTNNbItHzGy1PAYNTH36uNF8cHmXKkZk=";
pub const DEFAULT_PERMANENT_PASSWORD: &str = "ykgxZu9TmU4169GErxpr";
```
- 永久密码逻辑（允许用户覆盖；为空回落默认）：
```rust
pub fn set_permanent_password(pass: &str) { /* 允许保存用户密码 */ }
pub fn get_permanent_password() -> String { /* 先读保存；空 → DEFAULT_PERMANENT_PASSWORD */ }
```

2) `src/common.rs`
- API 默认兜底：
```rust
fn get_api_server_() -> String { "http://yc.xinsikeji.com:21114".to_string() }
```
- 启动注入默认项（不覆盖用户已保存）：
```rust
DEFAULT_SETTINGS.insert("custom-rendezvous-server".into(), "yc.xinsikeji.com:21116".into());
DEFAULT_SETTINGS.insert("relay-server".into(), "yc.xinsikeji.com:21117".into());
DEFAULT_SETTINGS.insert("api-server".into(), "http://yc.xinsikeji.com:21114".into());
DEFAULT_SETTINGS.insert("key".into(), "4fgpDL4LxpKBTNNbItHzGy1PAYNTH36uNF8cHmXKkZk=".into());
DEFAULT_SETTINGS.insert("enable-check-update".into(), "N".into());
DEFAULT_SETTINGS.insert("allow-auto-update".into(), "N".into());
DEFAULT_SETTINGS.insert("verification-method".into(), "use-both-passwords".into());
DEFAULT_SETTINGS.insert("approve-mode".into(), "password".into());

BUILTIN_SETTINGS.insert("hide-server-settings".into(), "Y".into());

if CONFIG.password.trim().is_empty() {
    Config::set_permanent_password(DEFAULT_PERMANENT_PASSWORD);
}
```

3) `src/ipc.rs`
```rust
pub fn get_permanent_password(/* ... */) -> String { Config::get_permanent_password() }
```

4) i18n（中文与英文至少中文必备）
- `src/lang/cn.rs`
```rust
("login_required_hint_under_input", "登录后才能控制其他设备"),
("login_required_dialog_title2", "需要登录"),
("login_required_dialog_body2", "未登录，无法控制其他设备。"),
("go_to_login", "去登录"),
("login_dialog_footer_note", "账号由管理员分配，暂不支持注册。"),
```
- `src/lang/en.rs`
```rust
("login_required_hint_under_input", "Login required to start remote control"),
("login_required_dialog_title2", "Login required"),
("login_required_dialog_body2", "Not logged in, unable to initiate remote control."),
("go_to_login", "Go to login"),
("login_dialog_footer_note", "Account assigned by administrator, registration is not supported."),
```

5) 桌面 UI：头像入口 → 账号页（遵循原有“被控蒙层”逻辑）
- `flutter/lib/desktop/pages/desktop_tab_page.dart`
```dart
Offstage(
  offstage: !(isWindows || isMacOS),
  child: Obx(() {
    final isLoggedIn = gFFI.userModel.isLogin;
    final color = isLoggedIn
        ? MyTheme.tabbar(context).selectedTabIconColor
        : MyTheme.tabbar(context).unSelectedIconColor;
    return InkWell(
      onTap: () async {
        final blocked = stateGlobal.videoConnCount > 0 && await canBeBlocked();
        if (gFFI.userModel.isLogin) {
          // 已登录：始终跳转到 设置-账号
          DesktopSettingPage.switch2page(SettingsTabKey.account);
        } else {
          // 未登录且被远程控制不允许改配置：进入设置（将被蒙层屏蔽）
          if (blocked) {
            DesktopSettingPage.switch2page(SettingsTabKey.account);
          } else {
            // 其余情况直接拉起登录
            loginDialog();
          }
        }
      },
      child: SizedBox(
        height: kDesktopRemoteTabBarHeight - 1,
        width: kDesktopRemoteTabBarHeight - 1,
        child: Icon(Icons.person, size: 14, color: color),
      ),
    );
  }),
),
```

6) 桌面 UI：输入框下未登录提示（颜色增强：亮#606060 / 暗#E0E0E0）
- `flutter/lib/desktop/pages/connection_page.dart`
```dart
Obx(() {
  final theme = Theme.of(context);
  final isDark = theme.brightness == Brightness.dark;
  final hintColor = isDark ? const Color(0xFFE0E0E0) : const Color(0xFF606060);
  return Offstage(
    offstage: !(isWindows || isMacOS) || gFFI.userModel.isLogin,
    child: Align(
      alignment: Alignment.centerLeft,
      child: Text(
        translate('login_required_hint_under_input'),
        style: TextStyle(fontSize: 14, color: hintColor),
      ).marginOnly(top: 10),
    ),
  );
}),
```

7) 桌面逻辑：连接前置拦截 + 统一登录对话框（minWidth=420；不改变原有蒙层策略）
- `flutter/lib/common.dart`
```dart
// connect(...) 顶部
if (isDesktop && (isWindows || isMacOS) && !gFFI.userModel.isLogin) {
  await showLoginRequiredDialog(context);
  return;
}

Future<void> showLoginRequiredDialog(BuildContext context) async {
  await gFFI.dialogManager.show((setState, close, ctx) {
    onGoLogin() { close(); loginDialog(); }
    return CustomAlertDialog(
      title: Text(translate('login_required_dialog_title2')),
      content: Text(translate('login_required_dialog_body2')),
      contentBoxConstraints: BoxConstraints(minWidth: 420),
      actions: [
        dialogButton(translate('Cancel'), onPressed: close, isOutline: true),
        dialogButton(translate('go_to_login'), onPressed: onGoLogin),
      ],
      onCancel: close,
      onSubmit: onGoLogin,
    );
  });
}
```

11) 桌面逻辑：应用启动时自动启动服务（Windows/macOS）
- `flutter/lib/main.dart`
```dart
// runMainApp(...) 内，在 bind.mainCheckConnectStatus() 之后：
if (isWindows || isMacOS) {
  final stopped = await mainGetBoolOption(kOptionStopService);
  if (stopped) {
    await start_service(true);
  }
}
```

8) 桌面兜底：服务端错误字符串拦截（minWidth=420；不改变原有蒙层策略）
- `flutter/lib/desktop/pages/desktop_home_page.dart`
```dart
final error = await bind.mainGetError();
if (error == "Connection failed, please login!" && (isWindows || isMacOS)
    && !gFFI.userModel.isLogin && !_loginPromptShown) {
  _loginPromptShown = true;
  gFFI.dialogManager.show((setState, close, context) {
    onGoLogin() { close(); _loginPromptShown = false; loginDialog(); }
    return CustomAlertDialog(
      title: Text(translate('login_required_dialog_title2')),
      content: Text(translate('login_required_dialog_body2')),
      contentBoxConstraints: BoxConstraints(minWidth: 420),
      actions: [
        dialogButton(translate('Cancel'), onPressed: () { _loginPromptShown = false; close(); }, isOutline: true),
        dialogButton(translate('go_to_login'), onPressed: onGoLogin),
      ],
      onCancel: () { _loginPromptShown = false; close(); },
      onSubmit: onGoLogin,
    );
  });
}
```

9) 桌面 UI：登录弹窗底部说明
- `flutter/lib/common/widgets/login.dart`
```dart
if (isDesktop)
  Padding(
    padding: const EdgeInsets.only(top: 10),
    child: Builder(builder: (context) {
      final textColor = Theme.of(context).textTheme.titleLarge?.color;
      return Text(
        translate('login_dialog_footer_note'),
        style: TextStyle(fontSize: 14, color: textColor?.withOpacity(0.5)),
        textAlign: TextAlign.center,
      );
    }),
  ),
```

10) 移动端/Sciter：隐藏入口
- `flutter/lib/mobile/pages/settings_page.dart`：移除 `ScanButton` 与 `scan_page.dart` 引用
- `flutter/lib/common/widgets/setting_widgets.dart`：
```dart
List<Widget> ServerConfigImportExportWidgets() { return []; }
```
- `src/ui/index.tis`（如使用 Sciter）：注释 `<li #custom-server>`

---

### D. 锚点检查（可选，ripgrep）

```bash
# 在项目根运行，逐项应能命中
rg -n "RENDEZVOUS_SERVERS|RS_PUB_KEY|DEFAULT_PERMANENT_PASSWORD|fn set_permanent_password|fn get_permanent_password" libs/hbb_common/src/config.rs
rg -n "fn get_api_server_|fn load_custom_client|custom-rendezvous-server|relay-server|api-server|key|enable-check-update|allow-auto-update|verification-method|approve-mode|hide-server-settings" src/common.rs
rg -n "get_permanent_password\(|Config::get_permanent_password" src/ipc.rs
rg -n "login_required_hint_under_input|login_required_dialog_title2|login_required_dialog_body2|go_to_login|login_dialog_footer_note" src/lang/cn.rs src/lang/en.rs
rg -n "Icons.person|DesktopSettingPage.switch2page\(SettingsTabKey.account\)|loginDialog\(|canBeBlocked\(" flutter/lib/desktop/pages/desktop_tab_page.dart
rg -n "login_required_hint_under_input|0xFF606060|0xFFE0E0E0|marginOnly\(top: 10\)" flutter/lib/desktop/pages/connection_page.dart
rg -n "connect\(BuildContext|showLoginRequiredDialog\(|contentBoxConstraints: BoxConstraints\(minWidth: 420\)|dialogButton\(translate\('go_to_login'\)|dialogButton\(translate\('Cancel'\)" flutter/lib/common.dart
rg -n "Connection failed, please login!|login_required_dialog_title2|login_required_dialog_body2|contentBoxConstraints: BoxConstraints\(minWidth: 420\)|_loginPromptShown" flutter/lib/desktop/pages/desktop_home_page.dart
rg -n "login_dialog_footer_note|withOpacity\(0.5\)|EdgeInsets.only\(top: 10\)" flutter/lib/common/widgets/login.dart
rg -n "ScanButton|scan_page\.dart|ServerConfigImportExportWidgets\(|#custom-server" -S flutter/lib/mobile/pages/settings_page.dart flutter/lib/common/widgets/setting_widgets.dart src/ui/index.tis
rg -n "runMainApp\(|mainCheckConnectStatus\(|kOptionStopService|start_service\(true\)" flutter/lib/main.dart
```

---

### E. 版本与回滚

```
TAG=v1.4.2-yc-internal-20250907
ROLLBACK: git reset --hard v1.4.2-yc-internal-20250907 && git push -f origin master
```

---

备注：
- 本文件为“单文件版”，已并入上下文片段与校验命令；无需依赖其他文档即可复刻。
- 如上游结构发生变化，请优先用锚点检索定位，再以片段作比对替换关键行。


