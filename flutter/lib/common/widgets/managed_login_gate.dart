import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_hbb/common.dart';
import 'package:flutter_hbb/models/platform_model.dart';

class ManagedLoginGate extends StatefulWidget {
  const ManagedLoginGate({super.key, required this.child});
  final Widget child;

  @override
  State<ManagedLoginGate> createState() => _ManagedLoginGateState();
}

class _ManagedLoginGateState extends State<ManagedLoginGate> {
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _device = TextEditingController();
  Timer? _timer;
  bool _enabled = false;
  bool _busy = false;
  String _phase = 'loading';
  String _error = '';
  String _account = '';

  @override
  void initState() {
    super.initState();
    _enabled = bind.mainGetBuildinOption(key: 'managed-login') == 'Y';
    _device.text = isAndroid ? '我的 Android' : isMacOS ? '我的 Mac' : '我的电脑';
    if (_enabled) {
      _invoke({'operation': 'status'});
      _timer = Timer.periodic(const Duration(seconds: 10), (_) {
        _invoke({'operation': 'status'});
      });
    }
  }

  Future<void> _invoke(Map<String, dynamic> command) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final raw = await bind.mainManagedAuth(command: jsonEncode(command));
      final result = jsonDecode(raw) as Map<String, dynamic>;
      if (result['ok'] != true) {
        throw result['error'] ?? '登录服务暂时不可用';
      }
      final status = result['result'] as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _phase = status['state'] as String;
        _account = status['username'] as String;
        if (_username.text.isEmpty) _username.text = _account;
        _error = '';
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.toString();
        if (_phase == 'loading') _phase = 'restoring';
        if (command['operation'] == 'logout') {
          _phase = 'signed_out';
        }
      });
    } finally {
      if (command['operation'] == 'login') _password.clear();
      if (mounted) setState(() => _busy = false);
    }
  }

  void _login() {
    if (_username.text.trim().isEmpty || _password.text.isEmpty ||
        _device.text.trim().isEmpty) {
      setState(() => _error = '请填写账号、密码和设备名称');
      return;
    }
    _invoke({
      'operation': 'login',
      'username': _username.text.trim(),
      'password': _password.text,
      'device_name': _device.text.trim(),
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _username.dispose();
    _password.dispose();
    _device.dispose();
    super.dispose();
  }

  Widget _logout() => TextButton(
    onPressed: _busy ? null : () => _invoke({'operation': 'logout'}),
    child: const Text('退出账号'),
  );

  @override
  Widget build(BuildContext context) {
    if (!_enabled) return widget.child;
    if (_phase == 'approved') {
      return Column(children: [
        Material(
          color: Theme.of(context).colorScheme.surface,
          child: SafeArea(bottom: false, child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(children: [
              const Icon(Icons.verified_user_outlined, size: 18),
              const SizedBox(width: 8),
              Expanded(child: Text('$_account · 设备已批准',
                  overflow: TextOverflow.ellipsis)),
              _logout(),
            ]),
          )),
        ),
        if (_error.isNotEmpty)
          Material(child: Padding(
            padding: const EdgeInsets.all(8),
            child: Text(_error, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          )),
        Expanded(child: widget.child),
      ]);
    }
    final restoring = _phase == 'restoring';
    final pending = _phase == 'pending' || restoring;
    return Scaffold(
      body: SafeArea(child: Center(child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Icon(pending ? Icons.hourglass_top : Icons.lock_outline, size: 48),
            const SizedBox(height: 20),
            Text(restoring ? '恢复已保存的登录' : pending ? '等待设备批准' : '登录内部远程协助',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 12),
            Text(restoring ? '连接恢复后会自动检查已保存的登录。也可以手动重试。' : pending
                ? '请联系管理员在网页后台批准此设备。批准后会自动进入，之后可保持登录。'
                : '使用管理员分配的账号。新设备首次使用需要批准。',
                textAlign: TextAlign.center),
            const SizedBox(height: 24),
            if (!pending && _phase != 'loading') ...[
              TextField(controller: _username, enabled: !_busy,
                  autocorrect: false,
                  decoration: const InputDecoration(labelText: '账号')),
              const SizedBox(height: 12),
              TextField(controller: _password, enabled: !_busy,
                  obscureText: true, enableSuggestions: false, autocorrect: false,
                  onSubmitted: (_) => _login(),
                  decoration: const InputDecoration(labelText: '密码')),
              const SizedBox(height: 12),
              TextField(controller: _device, enabled: !_busy, maxLength: 80,
                  decoration: const InputDecoration(labelText: '设备名称')),
              const SizedBox(height: 16),
              FilledButton(onPressed: _busy ? null : _login,
                  child: const Text('登录')),
            ],
            if (_error.isNotEmpty) Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Text(_error, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ),
            if (_busy) const Padding(
              padding: EdgeInsets.all(12),
              child: Center(child: CircularProgressIndicator()),
            ),
            TextButton(onPressed: _busy ? null : () => _invoke({'operation': 'status'}),
                child: const Text('重新检查状态')),
            if (pending || _account.isNotEmpty) _logout(),
            if (isDesktop) ...[
              const Divider(),
              const Text('电脑需要安装后台服务，才能在关闭窗口或重启后保持可连接。',
                  textAlign: TextAlign.center),
              TextButton(
                onPressed: () {
                  if (!bind.mainIsInstalled()) {
                    bind.mainGotoInstall();
                  } else if (isMacOS) {
                    bind.mainIsInstalledDaemon(prompt: true);
                  } else {
                    bind.mainStartService();
                  }
                },
                child: const Text('安装或启动后台服务'),
              ),
            ],
          ]),
        ),
      ))),
    );
  }
}
