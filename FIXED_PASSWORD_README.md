# RustDesk 固定密码配置修改说明

## 修改概述

我们已经成功修改了RustDesk的源代码，设置了固定的连接密码 `ykgxZu9TmU4169GErxpr`。这个修改将应用于所有平台（Windows、macOS、Linux、Android、iOS）。

## 修改位置

**文件**: `libs/hbb_common/src/config.rs`  
**行数**: 72-76

## 修改内容

```rust
pub static ref BUILTIN_SETTINGS: RwLock<HashMap<String, String>> = {
    let mut settings = HashMap::new();
    // 设置默认的固定连接密码
    settings.insert("default-connect-password".to_string(), "ykgxZu9TmU4169GErxpr".to_string());
    RwLock::new(settings)
};
```

## 工作原理

1. **BUILTIN_SETTINGS**: 这是RustDesk的内置设置集合，在程序启动时自动初始化
2. **default-connect-password**: 这是RustDesk支持的配置选项，用于设置默认的连接密码
3. **跨平台支持**: 由于这个设置在核心配置模块中，所以所有平台都会使用相同的密码

## 密码优先级

当客户端尝试连接时，RustDesk会按以下顺序查找密码：

1. 预设密码（preset password）
2. 共享密码（shared password）  
3. 对等配置密码（peer config password）
4. **默认连接密码（default-connect-password）** ← 我们设置的固定密码
5. 个人地址簿密码（personal ab password）

## 安全性说明

⚠️ **重要提醒**:
- 固定密码虽然方便，但会降低安全性
- 建议在生产环境中：
  - 定期更换密码
  - 结合其他安全措施（如IP白名单）
  - 限制访问权限

## 编译说明

修改完成后，需要重新编译RustDesk：

```bash
# Windows
cargo build --release --target x86_64-pc-windows-msvc

# macOS  
cargo build --release --target x86_64-apple-darwin
cargo build --release --target aarch64-apple-darwin

# Linux
cargo build --release --target x86_64-unknown-linux-gnu
```

## 验证方法

编译完成后，可以通过以下方式验证：

1. 启动RustDesk客户端
2. 尝试连接远程设备
3. 如果远程设备没有设置密码，应该会自动使用固定密码 `ykgxZu9TmU4169GErxpr`

## 注意事项

- 这个修改是全局性的，会影响所有使用此编译版本的客户端
- 如果需要不同的密码，可以修改源代码中的密码值
- 建议在分发客户端前测试连接功能是否正常
