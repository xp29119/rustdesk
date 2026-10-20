#!/usr/bin/env python3
"""Prepare/activate only after user approval; does not alter existing Mac services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys

BASE = Path.home() / "Library/Application Support/RDControlBridge"
AGENTS = Path.home() / "Library/LaunchAgents"
LABELS = ("local.rdcontrol.cert-proxy", "local.rdcontrol.mac-link")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def check_ports():
    for port in (18488, 18490):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))


def private_file(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


def prepare(fingerprint, expected_host):
    if not expected_host or run("/usr/sbin/scutil", "--get", "LocalHostName").strip() != expected_host:
        raise SystemExit("This is not the explicitly selected Mac")
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint):
        raise SystemExit("Supply the trusted server's Ed25519 SHA256 fingerprint")
    if BASE.exists():
        raise SystemExit("Bridge directory already exists; inspect before changing it")
    if any((AGENTS / (label + ".plist")).exists() for label in LABELS):
        raise SystemExit("A matching launch agent already exists; refusing to replace it")
    check_ports()
    python = next((p for p in ("/opt/homebrew/bin/python3", "/usr/bin/python3")
                   if Path(p).is_file()), None)
    if not python:
        raise SystemExit("A persistent Python 3 installation is required")
    version = json.loads(run(python, "-c", "import sys,json; print(json.dumps(list(sys.version_info[:2])))"))
    if version < [3, 9]:
        raise SystemExit("Python 3.9 or later is required")
    BASE.mkdir(mode=0o700)
    candidate = BASE / "known_hosts.candidate"
    try:
        scan = run("/usr/bin/ssh-keyscan", "-T", "8", "-t", "ed25519", "8.138.230.183")
        lines = [line for line in scan.splitlines() if line and not line.startswith("#")]
        if len(lines) != 1:
            raise RuntimeError("Unexpected host-key scan")
        private_file(candidate, (lines[0] + "\n").encode())
        observed = run("/usr/bin/ssh-keygen", "-lf", str(candidate)).split()[1]
        if observed != fingerprint:
            raise RuntimeError("Server fingerprint mismatch; nothing was trusted")
        candidate.replace(BASE / "known_hosts")
        identity = BASE / "identity"
        run("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(identity),
            "-C", "rdcontrol-mac-link")
        identity.chmod(0o600)
        proxy = BASE / "mac_cert_proxy.py"
        private_file(proxy, (Path(__file__).parent / "mac_cert_proxy.py").read_bytes())
        ssh_args = [
            "/usr/bin/ssh", "-F", "/dev/null", "-N", "-T",
            "-i", str(identity), "-o", "UserKnownHostsFile=" + str(BASE / "known_hosts"),
            "-o", "StrictHostKeyChecking=yes", "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
            "-o", "PreferredAuthentications=publickey", "-o", "PasswordAuthentication=no",
            "-o", "KbdInteractiveAuthentication=no", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-L", "127.0.0.1:18490:127.0.0.1:8088",
            "-R", "127.0.0.1:18489:127.0.0.1:18488",
            "rd-mac-webcert@8.138.230.183",
        ]
        programs = [
            [python, str(proxy), "--port", "18488", "--upstream-port", "6152",
             "--log-file", str(BASE / "proxy.log")],
            ssh_args,
        ]
        for label, argv in zip(LABELS, programs):
            contents = {
                "Label": label, "ProgramArguments": argv,
                "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 15,
                "ProcessType": "Background", "Umask": 0o077,
                "WorkingDirectory": str(BASE),
                # No tunnel traffic or unbounded SSH diagnostic logs are stored.
                "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
            }
            private_file(BASE / (label + ".plist"), plistlib.dumps(contents))
        private_file(BASE / "state.json", json.dumps({
            "version": 1, "server_fingerprint": fingerprint,
            "expected_host": expected_host,
            "proxy_sha256": hashlib.sha256(proxy.read_bytes()).hexdigest(),
            "labels": LABELS, "activated": False,
        }, indent=2).encode())
    except Exception:
        # Keep partial files for inspection; never overwrite keys on a blind retry.
        raise
    print("Prepared only; no LaunchAgent was installed or started.")
    print((BASE / "identity.pub").read_text().strip())
    print(run("/usr/bin/ssh-keygen", "-lf", str(BASE / "identity.pub")).strip())


def activate():
    state = json.loads((BASE / "state.json").read_text())
    if run("/usr/sbin/scutil", "--get", "LocalHostName").strip() != state["expected_host"]:
        raise SystemExit("This is not the prepared Mac")
    if state["version"] != 1 or state["activated"]:
        raise SystemExit("Inspect existing bridge state before activation")
    if hashlib.sha256((BASE / "mac_cert_proxy.py").read_bytes()).hexdigest() != state["proxy_sha256"]:
        raise SystemExit("Prepared proxy changed; review again before activation")
    check_ports()
    AGENTS.mkdir(exist_ok=True)
    if any((AGENTS / (label + ".plist")).exists() for label in LABELS):
        raise SystemExit("Refusing to replace existing agents")
    installed = []
    try:
        for label in LABELS:
            target = AGENTS / (label + ".plist")
            private_file(target, (BASE / (label + ".plist")).read_bytes())
            installed.append(target)
            run("/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(target))
    except Exception:
        for target in reversed(installed):
            subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(target)],
                           capture_output=True)
            target.rename(BASE / (target.name + ".failed"))
        raise
    state["activated"] = True
    private_file(BASE / "state.json", json.dumps(state, indent=2).encode())
    print("Agents loaded. Verify actual forwarding, destination denial and certificate issuance.")
    print("Existing Seqdao, DDNS, Surge and Caddy services were not changed.")


def status():
    for label in LABELS:
        result = subprocess.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
                                text=True, capture_output=True)
        fields = [line.strip() for line in result.stdout.splitlines()
                  if re.match(r"\s*(state|pid|last exit code) =", line)]
        print(label + ": " + ("; ".join(fields) if fields else "not loaded"))


def main():
    if sys.platform != "darwin" or os.geteuid() == 0:
        raise SystemExit("Run as the logged-in ordinary Mac user")
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--activate", action="store_true")
    mode.add_argument("--status", action="store_true")
    parser.add_argument("--server-fingerprint", default="")
    parser.add_argument("--expected-host", default="")
    args = parser.parse_args()
    if args.prepare:
        prepare(args.server_fingerprint, args.expected_host)
    elif args.activate:
        activate()
    else:
        status()


if __name__ == "__main__":
    main()
