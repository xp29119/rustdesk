#!/usr/bin/env python3
"""Install RustDesk Server OSS 1.1.16 on the inspected ali gz host."""
import hashlib
import grp
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import time
import zipfile

ARCHIVE = Path("/tmp/rustdesk-server.zip")
SHA256 = "0565c41affe6c3f409b0bddfaf5a24ccbf3f64f5f8e3fec250b69a0d5f6bdbcf"
INSTALL = Path("/opt/rustdesk-server-1.1.16")
STATE = Path("/var/lib/rustdesk-server")
FIREWALL = Path("/etc/nftables.conf")
SERVER = "8.138.230.183"


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run as root.")
    if os.uname().machine != "x86_64":
        raise SystemExit("This installer requires x86_64.")
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("Official archive checksum mismatch; nothing installed.")
    if INSTALL.exists() or STATE.exists():
        raise SystemExit("Existing installation/data found; inspect before changing it.")
    for service in ("hbbs", "hbbr"):
        if Path(f"/etc/systemd/system/rustdesk-{service}.service").exists():
            raise SystemExit("Existing systemd unit found; refusing to overwrite.")
    original = FIREWALL.read_text()
    anchor = "tcp dport 22 accept"
    if original.count(anchor) != 1 or "table inet vpn_filter" not in original:
        raise SystemExit("Firewall differs from the inspected configuration.")
    if "21116" in original:
        raise SystemExit("Existing RustDesk firewall entries; inspect first.")
    updated = original.replace(
        anchor,
        anchor + '\n        tcp dport { 21115, 21116, 21117 } accept comment "RustDesk"\n'
        '        udp dport 21116 accept comment "RustDesk"',
    )
    candidate = Path("/run/rustdesk-nftables-check.conf")
    candidate.write_text(updated)
    run("nft", "--check", "--file", str(candidate))
    try:
        pwd.getpwnam("rustdesk")
    except KeyError:
        run("useradd", "--system", "--home-dir", str(STATE),
            "--shell", "/usr/sbin/nologin", "rustdesk")
    account = pwd.getpwnam("rustdesk")
    INSTALL.mkdir(mode=0o755)
    STATE.mkdir(mode=0o700)
    os.chown(STATE, account.pw_uid, account.pw_gid)
    with zipfile.ZipFile(ARCHIVE) as archive:
        for name in ("hbbs", "hbbr"):
            matches = [p for p in archive.namelist() if p.endswith("/" + name)]
            if len(matches) != 1:
                raise SystemExit(f"Unexpected archive contents for {name}")
            target = INSTALL / name
            target.write_bytes(archive.read(matches[0]))
            target.chmod(0o755)
    for name in ("hbbs", "hbbr"):
        arguments = f"-r {SERVER}:21117 -k _" if name == "hbbs" else "-k _"
        unit = f"""[Unit]
Description=RustDesk OSS {name}
After=network-online.target
Wants=network-online.target

[Service]
User=rustdesk
Group={grp.getgrgid(account.pw_gid).gr_name}
WorkingDirectory={STATE}
ExecStart={INSTALL}/{name} {arguments}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={STATE}
LimitNOFILE=100000

[Install]
WantedBy=multi-user.target
"""
        Path(f"/etc/systemd/system/rustdesk-{name}.service").write_text(unit)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", "rustdesk-hbbs.service")
    public_key = STATE / "id_ed25519.pub"
    for _ in range(50):
        if public_key.exists() and public_key.stat().st_size:
            break
        time.sleep(0.1)
    else:
        raise SystemExit("hbbs did not generate its key; firewall has not been opened.")
    run("systemctl", "enable", "--now", "rustdesk-hbbr.service")
    time.sleep(1)
    for name in ("hbbs", "hbbr"):
        run("systemctl", "is-active", f"rustdesk-{name}.service")
    backup = Path("/etc/nftables.conf.before-rustdesk")
    if backup.exists():
        raise SystemExit("Firewall backup already exists; inspect before changing firewall.")
    shutil.copy2(FIREWALL, backup)
    # Append only these rules to the live input chain. Do not flush AWG/SSH state.
    run("nft", "add", "rule", "inet", "vpn_filter", "input",
        "tcp", "dport", "{", "21115,", "21116,", "21117", "}", "accept",
        "comment", '"RustDesk"')
    run("nft", "add", "rule", "inet", "vpn_filter", "input",
        "udp", "dport", "21116", "accept", "comment", '"RustDesk"')
    FIREWALL.write_text(updated)
    print("RustDesk Server OSS 1.1.16 is active.")
    print("ID server:", SERVER)
    print("Relay server:", SERVER + ":21117")
    print("PUBLIC KEY:", public_key.read_text().strip())
    print("Firewall backup:", backup)
    print("Alibaba Cloud security group and external connectivity still require verification.")


if __name__ == "__main__":
    main()
