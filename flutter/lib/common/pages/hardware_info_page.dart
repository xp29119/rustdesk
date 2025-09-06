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
      _KV('架构', m['arch']),
      _KV('平台', m['platform']),
      _KV('主机名', m['hostname']),
      _KV('用户名', m['username']),
      _KV('内网IP', (m['ips'] is List) ? (m['ips'] as List).join(', ') : m['ips']),
      _KV('MAC', m['mac']),
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
    final disks = (_data?['disks'] as List?)?.cast<Map<String, dynamic>>();
    final nics = (_data?['nics'] as List?)?.cast<Map<String, dynamic>>();
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
              : ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    _SectionCard(
                      title: '基本信息',
                      items: [
                        ..._rows()
                            .where((e) => ['电脑型号','主机名','操作系统','平台','架构','用户名','内网IP','MAC']
                                .contains(e.label))
                            .map((row) => _Item(label: row.label, value: '${row.value ?? ''}')),
                      ],
                    ),
                    const SizedBox(height: 12),
                    _SectionCard(
                      title: '处理器 / 内存',
                      items: [
                        ..._rows()
                            .where((e) => ['处理器','内存'].contains(e.label))
                            .map((row) => _Item(label: row.label, value: '${row.value ?? ''}')),
                      ],
                    ),
                    if (disks != null && disks.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      _SectionCard(
                        title: '磁盘',
                        items: [
                          ...disks.map((d) => _Item(
                                label: d['name']?.toString() ?? '',
                                value:
                                    '挂载:${d['mount'] ?? ''}  总:${d['total_gb']}GB  可用:${d['available_gb']}GB  FS:${d['fs'] ?? ''}',
                              )),
                        ],
                      ),
                    ],
                    if (nics != null && nics.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      _SectionCard(
                        title: '网络',
                        items: [
                          ...nics.map((n) => _Item(
                                label: n['name']?.toString() ?? '',
                                value: 'IPv4: ${n['ipv4'] ?? ''}',
                              )),
                        ],
                      ),
                    ],
                  ],
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
          width: 110,
          child: Text(label, style: Theme.of(context).textTheme.bodyMedium),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: SelectableText(
            value,
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        ),
      ],
    );
  }
}

class _SectionCard extends StatelessWidget {
  final String title;
  final List<Widget> items;
  const _SectionCard({required this.title, required this.items});
  @override
  Widget build(BuildContext context) {
    return Card(
      elevation: 0.5,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            ..._intersperse(items, const Divider(height: 16)),
          ],
        ),
      ),
    );
  }
}

List<Widget> _intersperse(List<Widget> list, Widget separator) {
  if (list.isEmpty) return list;
  return [
    for (int i = 0; i < list.length; i++) ...[
      list[i],
      if (i != list.length - 1) separator,
    ]
  ];
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


