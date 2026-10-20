#!/usr/bin/env python3
"""Apply only after approval: add one key-only, forwarding-only account on ali gz."""
import argparse
import base64
import hashlib
import os
from pathlib import Path
import pwd
import socket
import subprocess

LINK_USER = "rd-mac-webcert"
ACCOUNT_HOME = Path("/var/lib/rdcontrol-mac-link")
CONFIG = Path("/etc/ssh/sshd_config.d/62-rdcontrol-mac-link.conf")
EXPECTED_SERVER_KEY = "781afdd127b550e8c9382d6f3400854d271a0595f2a05a623589fd8644a7ec6a"
SSH_POLICY = f"""Match User {LINK_USER}
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding yes
    PermitOpen 127.0.0.1:8088
    PermitListen 127.0.0.1:18489
    GatewayPorts no
    AllowStreamLocalForwarding no
    MaxSessions 0
    PermitTTY no
    PermitUserRC no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    ForceCommand /bin/false
Match all
"""


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("public_key_file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    parts = args.public_key_file.read_text().strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise SystemExit("Expected one Ed25519 public key")
    raw = base64.b64decode(parts[1], validate=True)
    if len(raw) != 51 or not raw.startswith(b"\0\0\0\x0bssh-ed25519\0\0\0\x20"):
        raise SystemExit("Invalid public key")
    print(run("ssh-keygen", "-lf", str(args.public_key_file)).strip())
    if not args.apply:
        print(SSH_POLICY)
        print("Dry run only. No account, key authorization, or SSH reload.")
        return
    if os.geteuid() != 0 or os.uname().sysname != "Linux":
        raise SystemExit("Apply as root on the intended Linux server")
    server_key = Path("/var/lib/rustdesk-server/id_ed25519.pub")
    if hashlib.sha256(server_key.read_bytes()).hexdigest() != EXPECTED_SERVER_KEY:
        raise SystemExit("This is not the verified RustDesk server")
    if CONFIG.exists() or ACCOUNT_HOME.exists():
        raise SystemExit("Dedicated configuration already exists; inspect before changing it")
    try:
        pwd.getpwnam(LINK_USER)
    except KeyError:
        pass
    else:
        raise SystemExit("Account already exists; refusing to change another account")
    # Do not displace any existing loopback service.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18489))
    os.umask(0o077)
    run("useradd", "--system", "--create-home", "--home-dir", str(ACCOUNT_HOME),
        "--shell", "/usr/sbin/nologin", LINK_USER)
    # An impossible password hash permits public-key-only account checks without a password login.
    run("usermod", "--password", "*", LINK_USER)
    account = pwd.getpwnam(LINK_USER)
    ACCOUNT_HOME.chmod(0o700)
    ssh = ACCOUNT_HOME / ".ssh"
    ssh.mkdir(mode=0o700)
    os.chown(ssh, account.pw_uid, account.pw_gid)
    authorized = ssh / "authorized_keys"
    authorized.write_text(
        'restrict,port-forwarding,command="/bin/false",'
        'permitopen="127.0.0.1:8088",permitlisten="127.0.0.1:18489" '
        + " ".join(parts[:2]) + " rdcontrol-mac-link\n"
    )
    authorized.chmod(0o600)
    os.chown(authorized, account.pw_uid, account.pw_gid)
    CONFIG.write_text(SSH_POLICY)
    try:
        run("sshd", "-t")
        effective = run("sshd", "-T", "-C", f"user={LINK_USER},host=8.138.230.183,addr=127.0.0.1")
        for line in (
            "authenticationmethods publickey", "passwordauthentication no",
            "allowtcpforwarding yes", "permitopen 127.0.0.1:8088",
            "permitlisten 127.0.0.1:18489", "gatewayports no",
            "allowstreamlocalforwarding no", "maxsessions 0", "permittty no",
            "permituserrc no", "allowagentforwarding no", "x11forwarding no",
            "permittunnel no", "forcecommand /bin/false",
        ):
            if line not in effective.splitlines():
                raise RuntimeError("SSH restriction not effective: " + line)
        run("systemctl", "reload", "ssh")
    except Exception:
        CONFIG.rename(CONFIG.with_suffix(".conf.disabled"))
        authorized.rename(authorized.with_suffix(".disabled"))
        raise
    print("Ready: forwarding-only account; no sudo, shell, commands, SFTP or public proxy.")
    print("Only local destination 127.0.0.1:8088 and remote listener 127.0.0.1:18489.")
    print("Revoke by renaming this account's authorized_keys; keep other accounts unchanged.")


if __name__ == "__main__":
    main()
