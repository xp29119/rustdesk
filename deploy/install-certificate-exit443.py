#!/usr/bin/env python3
"""Run on 194.156.163.226 only, after authorization to add the restricted account."""
import argparse
import socket
import base64
import os
from pathlib import Path
import pwd
import subprocess


def run(args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("public_key_file", nargs="?", default="/tmp/rdcontrol-exit-key.pub")
    args = parser.parse_args()
    probe = socket.socket()
    probe.bind(("0.0.0.0", 443))
    probe.close()
    Path(args.public_key_file).write_text('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOOMQQcfm3fCVX6bvK3Smr9pdn0GSupALxBW6Tpq8yEk rdcontrol-certificate-only\n')
    if os.geteuid() != 0:
        raise SystemExit("Root required")
    os.umask(0o077)
    key_file = Path(args.public_key_file)
    parts = key_file.read_text().strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise SystemExit("Expected one Ed25519 public key")
    raw = base64.b64decode(parts[1], validate=True)
    if len(raw) != 51:
        raise SystemExit("Invalid Ed25519 key")
    run(["ssh-keygen", "-lf", str(key_file)])
    config = Path("/etc/ssh/sshd_config.d/60-rdcontrol-certificate.conf")
    if config.exists():
        raise SystemExit("Account configuration already exists; inspect before updating")
    try:
        pwd.getpwnam("rd-cert")
    except KeyError:
        run(["useradd", "--system", "--create-home", "--home-dir", "/var/lib/rd-cert",
             "--shell", "/usr/sbin/nologin", "rd-cert"])
    else:
        raise SystemExit("rd-cert already exists; refusing to change another account")
    ssh = Path("/var/lib/rd-cert/.ssh")
    ssh.mkdir(mode=0o700)
    account = pwd.getpwnam("rd-cert")
    os.chown(ssh, account.pw_uid, account.pw_gid)
    authorized = ssh / "authorized_keys"
    authorized.write_text(
        'from="8.138.230.183",restrict,port-forwarding,'
        'permitopen="acme-v02.api.letsencrypt.org:443",'
        'permitopen="acme-staging-v02.api.letsencrypt.org:443" '
        + " ".join(parts[:2]) + " rdcontrol-certificate-only\n"
    )
    authorized.chmod(0o600)
    os.chown(authorized, account.pw_uid, account.pw_gid)
    config.write_text("""Match User rd-cert
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    AllowStreamLocalForwarding no
    PermitOpen acme-v02.api.letsencrypt.org:443 acme-staging-v02.api.letsencrypt.org:443
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    PermitTunnel no
    MaxSessions 0
    ForceCommand /bin/false
Match all
""")
    try:
        run(["sshd", "-t"])
        effective = run(["sshd", "-T", "-C",
                         "user=rd-cert,host=194.156.163.226,addr=8.138.230.183"])
        for expected in (
            "allowtcpforwarding local", "maxsessions 0", "forcecommand /bin/false",
            "passwordauthentication no",
            "permitopen acme-v02.api.letsencrypt.org:443 acme-staging-v02.api.letsencrypt.org:443",
        ):
            if expected not in effective.splitlines():
                raise RuntimeError("SSH restriction not effective: " + expected)
    except Exception:
        # The live SSH daemon remains untouched when validation fails.
        config.rename(config.with_suffix(".conf.disabled"))
        authorized.rename(authorized.with_suffix(".disabled"))
        raise
    run(["systemctl", "reload", "ssh"])
    dedicated = Path("/etc/ssh/rdcontrol-certificate-sshd.conf")
    if dedicated.exists():
        raise SystemExit("Dedicated certificate listener already exists")
    dedicated.write_text("""Port 443
ListenAddress 0.0.0.0
HostKey /etc/ssh/ssh_host_ed25519_key
PidFile /run/rdcontrol-certificate-sshd.pid
AllowUsers rd-cert
PermitRootLogin no
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM yes
AllowTcpForwarding local
AllowStreamLocalForwarding no
PermitOpen acme-v02.api.letsencrypt.org:443 acme-staging-v02.api.letsencrypt.org:443
PermitTTY no
X11Forwarding no
AllowAgentForwarding no
PermitTunnel no
MaxSessions 0
ForceCommand /bin/false
""")
    run(["sshd", "-t", "-f", str(dedicated)])
    Path("/etc/systemd/system/rdcontrol-certificate-exit.service").write_text("""[Unit]
Description=Restricted certificate SSH endpoint
After=network.target

[Service]
ExecStart=/usr/sbin/sshd -D -e -f /etc/ssh/rdcontrol-certificate-sshd.conf
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
""")
    run(["ufw", "allow", "proto", "tcp", "from", "8.138.230.183", "to", "any", "port", "443", "comment", "rdcontrol-cert-only"])
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "rdcontrol-certificate-exit.service"])
    run(["systemctl", "is-active", "rdcontrol-certificate-exit.service"])
    print("Dedicated SSH certificate listener active on TCP 443, source 8.138.230.183 only.")
    print("Restricted certificate account ready. Existing login accounts were not changed.")
    print("Allowed: source 8.138.230.183, ACME production/staging on TCP 443 only.")
    print("No shell, SFTP, reverse forwarding, public proxy listener, or agent forwarding.")


if __name__ == "__main__":
    main()
