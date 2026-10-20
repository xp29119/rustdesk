#!/usr/bin/env python3
"""Run on 8.138.230.183 only; prepare a certificate-only SSH tunnel."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

EXIT = "194.156.163.226"
FINGERPRINT = "SHA256:E8ZPynQ4ZbYLvlY9Jz/+LHKHzj5WzbtmIQ9seRDnbRs"
ROOT = Path("/etc/rdcontrol-cert")


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Root required")
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    key = ROOT / "identity"
    if not key.exists():
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
             "rdcontrol-certificate-only", "-f", str(key)])
    scan = run(["ssh-keyscan", "-T", "8", "-p", "443", "-t", "ed25519", EXIT])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".known_hosts") as tmp:
        tmp.write(scan)
        tmp.flush()
        fingerprint = run(["ssh-keygen", "-lf", tmp.name]).split()[1]
    if fingerprint != FINGERPRINT:
        raise SystemExit("Exit host fingerprint mismatch; no connection authorized")
    (ROOT / "known_hosts").write_text(scan)
    print("Public key to authorize on the exit host:")
    print((ROOT / "identity.pub").read_text().strip())
    print(run(["ssh-keygen", "-lf", str(ROOT / "identity.pub")]).strip())
    if not args.activate:
        print("Prepared only. Run with --activate after the exit host is configured.")
        return
    unit = """[Unit]
Description=Certificate-only SSH egress for control platform
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/ssh -NT -p 443 -D 127.0.0.1:1080 -i /etc/rdcontrol-cert/identity -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/rdcontrol-cert/known_hosts -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ConnectTimeout=10 rd-cert@194.156.163.226
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
"""
    path = Path("/etc/systemd/system/rdcontrol-cert-tunnel.service")
    if path.exists() and path.read_text() != unit:
        raise SystemExit("Existing tunnel unit differs; refusing overwrite")
    path.write_text(unit)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "rdcontrol-cert-tunnel.service"])
    print("Tunnel activated. Verify ACME through socks5h://127.0.0.1:1080 before enabling Caddy.")


if __name__ == "__main__":
    main()
