#!/usr/bin/env python3
"""Offline installation on ali gz. HTTPS activation is a separate explicit step."""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tarfile
import time
import urllib.request
import zipfile

BUNDLE_SHA256 = "BUNDLE_SHA256_PLACEHOLDER"
BUNDLE = Path(__file__).resolve().parent / "rdcontrol-bundle.tar.gz"
ROOT = Path("/opt/rdcontrol")


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def account(name, home):
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        run("useradd", "--system", "--home-dir", str(home), "--shell", "/usr/sbin/nologin", name)
        user = pwd.getpwnam(name)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(home, user.pw_uid, user.pw_gid)
    home.chmod(0o700)
    return user


def protect_native_ports():
    path = Path("/etc/nftables.conf")
    original = path.read_text()
    marker = 'comment "RDControl native isolation"'
    if marker in original:
        return
    if original.count("tcp dport 22 accept") != 1 or "table inet vpn_filter" not in original:
        raise RuntimeError("Unexpected firewall configuration; no network changes applied")
    kept = "\n".join(line for line in original.splitlines() if 'comment "RustDesk"' not in line) + "\n"
    # Put native-port drops before established/related accepts; loopback remains usable.
    anchor = "ct state established,related accept"
    drops = (
        'iifname != "lo" tcp dport { 21115, 21116, 21117, 21118, 21119 } drop comment "RDControl native isolation"\n'
        '        iifname != "lo" udp dport 21116 drop comment "RDControl native isolation"\n        '
    )
    if anchor not in kept:
        raise RuntimeError("Expected firewall state rule not found")
    updated = kept.replace(anchor, drops + anchor, 1)
    updated = updated.replace(
        "tcp dport 22 accept",
        'tcp dport 22 accept\n        tcp dport { 80, 443 } accept comment "RDControl HTTPS"',
        1,
    )
    candidate = Path("/run/rdcontrol-nft-check.conf")
    candidate.write_text(updated)
    run("nft", "--check", "--file", str(candidate))
    live = json.loads(run("nft", "-j", "list", "chain", "inet", "vpn_filter", "input"))
    commands = [
        'insert rule inet vpn_filter input iifname != "lo" tcp dport { 21115, 21116, 21117, 21118, 21119 } drop comment "RDControl native isolation"',
        'insert rule inet vpn_filter input iifname != "lo" udp dport 21116 drop comment "RDControl native isolation"',
    ]
    for item in live["nftables"]:
        rule = item.get("rule", {})
        if rule.get("comment") == "RustDesk":
            commands.append(f'delete rule inet vpn_filter input handle {int(rule["handle"])}')
    commands.append('add rule inet vpn_filter input tcp dport { 80, 443 } accept comment "RDControl HTTPS"')
    transaction = Path("/run/rdcontrol-nft-transaction.conf")
    transaction.write_text("\n".join(commands) + "\n")
    run("nft", "--check", "--file", str(transaction))
    backup = Path("/etc/nftables.conf.before-rdcontrol")
    if backup.exists():
        raise RuntimeError("Firewall backup exists but migration marker is absent; inspect first")
    shutil.copy2(path, backup)
    run("nft", "--file", str(transaction))
    path.write_text(updated)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate-gateway", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0 or os.uname().machine != "x86_64":
        raise SystemExit("Run as root on the x86_64 ali gz server")
    os.umask(0o077)
    if hashlib.sha256(BUNDLE.read_bytes()).hexdigest() != BUNDLE_SHA256:
        raise SystemExit("Bundle checksum mismatch")
    ROOT.mkdir(mode=0o755, exist_ok=True)
    ROOT.chmod(0o755)
    release = ROOT / ("release-" + BUNDLE_SHA256[:12])
    if not release.exists():
        release.mkdir(mode=0o755)
        release.chmod(0o755)
        with tarfile.open(BUNDLE) as archive:
            archive.extractall(release, filter="data")
        vendor = release / "vendor"
        vendor.mkdir()
        for wheel in (release / "deploy/wheels").glob("*.whl"):
            with zipfile.ZipFile(wheel) as archive:
                for info in archive.infolist():
                    if Path(info.filename).is_absolute() or ".." in Path(info.filename).parts:
                        raise RuntimeError("Unsafe wheel path")
                archive.extractall(vendor)
        # Runtime files are root-owned and readable by the restricted service account.
        for p in release.rglob("*"):
            p.chmod(0o755 if p.is_dir() or p.name == "caddy" else 0o644)
        (release / "deploy/caddy").chmod(0o755)
    control_user = account("rdcontrol", Path("/var/lib/rdcontrol"))
    caddy_user = account("rdcontrol-caddy", Path("/var/lib/rdcontrol-caddy"))
    link = ROOT / "next"
    link.unlink(missing_ok=True)
    link.symlink_to(release)
    link.replace(ROOT / "current")
    for service in ("rdcontrol", "rdcontrol-caddy"):
        contents = (release / f"deploy/{service}.service").read_text()
        user = control_user if service == "rdcontrol" else caddy_user
        contents = contents.replace(f"Group={service}", f"Group={grp.getgrgid(user.pw_gid).gr_name}")
        Path(f"/etc/systemd/system/{service}.service").write_text(contents)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", "rdcontrol.service")
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8088/api/meta", timeout=2) as response:
                meta = json.load(response)
            break
        except OSError:
            time.sleep(0.5)
    else:
        raise RuntimeError("Control service did not become healthy")
    print("Control service healthy on 127.0.0.1:8088; setup required:", meta["setup_required"])
    print("Bootstrap code remains private at /var/lib/rdcontrol/bootstrap-token.txt")
    if not args.activate_gateway:
        print("HTTPS and RustDesk were NOT activated. Configure and verify certificate egress first.")
        return
    # Prove the restricted tunnel works before changing firewall or enabling public HTTPS.
    run("curl", "-fsS", "--proxy", "socks5h://127.0.0.1:1080", "--max-time", "15",
        "https://acme-v02.api.letsencrypt.org/directory")
    run(str(release / "deploy/caddy"), "validate", "--config", str(release / "deploy/Caddyfile"),
        "--adapter", "caddyfile")
    protect_native_ports()
    for service in ("hbbs", "hbbr"):
        dropin = Path(f"/etc/systemd/system/rustdesk-{service}.service.d")
        dropin.mkdir(exist_ok=True)
        contents = "[Service]\nIPAddressDeny=any\nIPAddressAllow=localhost\n"
        if service == "hbbs":
            contents += (
                "ExecStart=\n"
                "ExecStart=/opt/rustdesk-server-1.1.16/hbbs -r wss://8.138.230.183/ws/relay -k _\n"
            )
        (dropin / "control-isolation.conf").write_text(contents)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", "rustdesk-hbbs", "rustdesk-hbbr", "rdcontrol-caddy")
    print("Gateway activated. Verify the public certificate and anonymous access denial before enrolling clients.")


if __name__ == "__main__":
    main()
