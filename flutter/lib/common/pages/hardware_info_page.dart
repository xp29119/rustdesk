import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_hbb/common.dart';
import 'package:flutter_hbb/models/platform_model.dart';

class HardwareInfoPage extends StatefulWidget {
  const HardwareInfoPage({super.key});

  @override
  State<HardwareInfoPage> createState() => _HardwareInfoPageState();
}

class _HardwareInfoPageState extends State<HardwareInfoPage> {
  Map<String, dynamic>? _data;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final raw = await Future.value(platformFFI.ffiBind.mainGetSysinfo());
      final map = jsonDecode(raw) as Map<String, dynamic>;
      setState(() {
        _data = map;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
      });
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  List<_KV> _rows() {
    final m = _data ?? {};
    return [
      _KV('电脑型号', ''),
      _KV('操作系统', m['os']),
      _KV('处理器', m['cpu']),
      _KV('内存', m['memory']),
      _KV('主机名', m['hostname']),
      _KV('用户名', m['username']),
    ].where((e) => (e.value ?? '').toString().isNotEmpty || e.label == '电脑型号').toList();
  }

  String _toPrettyText() {
    final rows = _rows();
    return rows
        .where((e) => (e.value ?? '').toString().isNotEmpty)
        .map((e) => '${e.label}: ${e.value}')
        .join('\n');
  }

  @override
  Widget build(BuildContext context) {
    final actions = <Widget>[
      IconButton(
        tooltip: '刷新',
        icon: const Icon(Icons.refresh),
        onPressed: _load,
      ),
      IconButton(
        tooltip: '复制',
        icon: const Icon(Icons.copy),
        onPressed: _data == null
            ? null
            : () async {
                await Clipboard.setData(ClipboardData(text: _toPrettyText()));
                showToast('已复制');
              },
      ),
    ];

    return Scaffold(
      appBar: AppBar(
        title: const Text('硬件信息'),
        actions: actions,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? _ErrorView(error: _error!, onRetry: _load)
              : ListView.separated(
                  padding: const EdgeInsets.all(16),
                  itemCount: _rows().length,
                  itemBuilder: (_, i) {
                    final row = _rows()[i];
                    if (row.label == '电脑型号') {
                      return _Header(model: _data?['hostname'] ?? '');
                    }
                    return _Item(label: row.label, value: '${row.value ?? ''}');
                  },
                  separatorBuilder: (_, __) => const Divider(height: 16),
                ),
    );
  }
}

class _Header extends StatelessWidget {
  final String model;
  const _Header({required this.model});
  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const CircleAvatar(child: Icon(Icons.computer)),
        const SizedBox(width: 12),
        Expanded(
          child: Text(
            model.isEmpty ? '本机' : model,
            style: Theme.of(context).textTheme.titleMedium,
          ),
        ),
      ],
    );
  }
}

class _Item extends StatelessWidget {
  final String label;
  final String value;
  const _Item({required this.label, required this.value});
  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 80,
          child: Text(label, style: Theme.of(context).textTheme.bodyMedium),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Text(value, style: Theme.of(context).textTheme.bodyMedium),
        ),
      ],
    );
  }
}

class _ErrorView extends StatelessWidget {
  final String error;
  final VoidCallback onRetry;
  const _ErrorView({required this.error, required this.onRetry});
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text('加载失败: $error'),
          const SizedBox(height: 8),
          ElevatedButton(onPressed: onRetry, child: const Text('重试')),
        ],
      ),
    );
  }
}

class _KV {
  final String label;
  final dynamic value;
  _KV(this.label, this.value);
}


