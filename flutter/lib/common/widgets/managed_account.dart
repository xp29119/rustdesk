import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../common.dart';
import '../../models/managed_account_model.dart';
import 'login.dart';

/// Owns status polling without replacing or resizing the native RustDesk pages.
class ManagedAccountHost extends StatefulWidget {
  const ManagedAccountHost({super.key, required this.child});
  final Widget child;

  @override
  State<ManagedAccountHost> createState() => _ManagedAccountHostState();
}

class _ManagedAccountHostState extends State<ManagedAccountHost> {
  @override
  void initState() {
    super.initState();
    managedAccount.start();
  }

  @override
  void dispose() {
    managedAccount.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}

class ManagedAccountEntry extends StatelessWidget {
  const ManagedAccountEntry({super.key});

  @override
  Widget build(BuildContext context) {
    if (!managedAccount.enabled) return const SizedBox.shrink();
    return Obx(() => Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            TextButton.icon(
              onPressed: loginDialog,
              icon: Icon(
                  managedAccount.approved
                      ? Icons.account_circle_outlined
                      : Icons.person_outline,
                  size: 18),
              label: Text(
                  managedAccount.signedIn
                      ? managedAccount.username.value
                      : translate('Login'),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis),
            ),
            if (managedAccount.signedIn && !managedAccount.approved)
              Text(managedAccount.statusText,
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodySmall),
          ]),
        ));
  }
}

/// Embedded in RustDesk's existing Account card, not a separate application page.
class ManagedAccountDetails extends StatelessWidget {
  const ManagedAccountDetails({super.key});

  @override
  Widget build(BuildContext context) => Obx(() => Padding(
        padding: const EdgeInsets.only(left: 18, top: 8, bottom: 8),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (managedAccount.signedIn)
            Text(managedAccount.username.value,
                style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),
          Text(managedAccount.statusText),
          if (managedAccount.signedIn) ...[
            const SizedBox(height: 6),
            Text('当前设备：${managedAccount.deviceName}',
                style: Theme.of(context).textTheme.bodySmall),
          ],
          if (managedAccount.error.isNotEmpty)
            Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(managedAccount.error.value,
                    style:
                        TextStyle(color: Theme.of(context).colorScheme.error))),
          const SizedBox(height: 10),
          Wrap(spacing: 8, children: [
            OutlinedButton(
                onPressed: managedAccount.busy.value ? null : loginDialog,
                child: Text(
                    translate(managedAccount.signedIn ? 'Account' : 'Login'))),
            if (managedAccount.signedIn)
              TextButton(
                  onPressed: managedAccount.busy.value
                      ? null
                      : () async {
                          try {
                            await managedAccount.refresh();
                          } catch (_) {}
                        },
                  child: Text(translate('Refresh'))),
          ]),
        ]),
      ));
}
