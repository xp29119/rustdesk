import 'dart:async';
import 'dart:convert';

import 'package:get/get.dart';

import 'platform_model.dart';

final managedAccount = ManagedAccountModel();

class ManagedAccountModel {
  final phase = 'signed_out'.obs;
  final username = ''.obs;
  final deviceId = ''.obs;
  final error = ''.obs;
  final busy = false.obs;
  Timer? _timer;
  Future<void>? _active;

  bool get enabled => bind.mainGetBuildinOption(key: 'managed-login') == 'Y';
  bool get approved => phase.value == 'approved';
  bool get signedIn => phase.value != 'signed_out' && username.isNotEmpty;

  String get statusText {
    switch (phase.value) {
      case 'approved':
        return '设备已获准';
      case 'pending':
        return '等待管理员批准此设备';
      case 'restoring':
        return '正在恢复登录';
      case 'background':
        return '等待无人值守服务恢复';
      default:
        return '登录后使用远程连接';
    }
  }

  String get deviceName {
    try {
      final info = jsonDecode(bind.mainGetLoginDeviceInfo());
      final name = (info['name'] as String? ?? '').trim();
      if (name.isNotEmpty) {
        return name.length > 80 ? name.substring(0, 80) : name;
      }
    } catch (_) {}
    return 'RustDesk';
  }

  void start() {
    if (!enabled || _timer != null) return;
    refresh().catchError((_) {});
    _timer = Timer.periodic(const Duration(seconds: 10), (_) {
      if (_active == null) refresh().catchError((_) {});
    });
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
  }

  Future<void> refresh() {
    if (!enabled) return Future.value();
    return _active ?? _run({'operation': 'status'});
  }

  Future<void> login(String account, String password) async {
    try {
      await _active;
    } catch (_) {}
    await _run({
      'operation': 'login',
      'username': account.trim(),
      'password': password,
      'device_name': deviceName
    });
  }

  Future<void> logout() async {
    try {
      await _active;
    } catch (_) {}
    await _run({'operation': 'logout'});
  }

  Future<void> _run(Map<String, dynamic> command) {
    final operation = _invoke(command);
    _active = operation;
    return operation.whenComplete(() {
      if (identical(_active, operation)) _active = null;
    });
  }

  Future<void> _invoke(Map<String, dynamic> command) async {
    busy.value = true;
    try {
      final raw = await bind.mainManagedAuth(command: jsonEncode(command));
      final data = jsonDecode(raw) as Map<String, dynamic>;
      if (data['ok'] != true) throw data['error'] ?? '账号状态暂时不可用';
      final status = data['result'] as Map<String, dynamic>;
      phase.value = status['state'] as String;
      username.value = status['username'] as String;
      deviceId.value = status['device_id'] as String;
      error.value = '';
    } catch (e) {
      if (phase.value == 'approved') phase.value = 'restoring';
      error.value = e.toString();
      rethrow;
    } finally {
      busy.value = false;
    }
  }
}
