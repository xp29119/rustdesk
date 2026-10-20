#!/usr/bin/env python3
"""Enable the explicitly approved client-only CA; never install system trust."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

SERVER_KEY_SHA256 = "781afdd127b550e8c9382d6f3400854d271a0595f2a05a623589fd8644a7ec6a"
DATA = Path("/var/lib/rdcontrol-caddy")
ROOT_CERT = DATA / "caddy/pki/authorities/local/root.crt"
CONFIG = Path("/etc/rdcontrol/Caddyfile.internal")
OVERRIDE = Path("/etc/systemd/system/rdcontrol-caddy.service.d/internal-ca.conf")
PUBLIC_EXPORT = Path("/tmp/rdcontrol-public-root.crt")


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def main():
    if os.geteuid() != 0 or os.uname().sysname != "Linux":
        raise SystemExit("Run on the approved Linux server as root")
    if hashlib.sha256(Path("/var/lib/rustdesk-server/id_ed25519.pub").read_bytes()).hexdigest() != SERVER_KEY_SHA256:
        raise SystemExit("This is not the verified RustDesk server")
    template = (Path(__file__).parent / "Caddyfile").read_text()
    if "skip_install_trust" not in template or "tls internal" not in template or "issuer acme" in template:
        raise SystemExit("Expected the reviewed internal-only CA configuration")
    override = ("# RDControl approved dedicated CA\n[Service]\nExecStart=\n"
                "ExecStart=/opt/rdcontrol/current/deploy/caddy run --config "
                "/etc/rdcontrol/Caddyfile.internal --adapter caddyfile\n")
    for path, expected in ((CONFIG, template), (OVERRIDE, override)):
        if path.exists() and path.read_text() != expected:
            raise SystemExit("Refusing to replace a different configuration: " + str(path))
    if PUBLIC_EXPORT.exists():
        raise SystemExit("Public export already exists; inspect before repeating")
    os.umask(0o077)
    CONFIG.parent.mkdir(mode=0o755, exist_ok=True)
    CONFIG.parent.chmod(0o755)
    CONFIG.write_text(template)
    CONFIG.chmod(0o644)
    prefix = ["runuser", "-u", "rdcontrol-caddy", "--", "env",
              "XDG_DATA_HOME=" + str(DATA), "XDG_CONFIG_HOME=" + str(DATA),
              "/opt/rdcontrol/current/deploy/caddy"]
    adapted = json.loads(run(*prefix, "adapt", "--config", str(CONFIG), "--adapter", "caddyfile"))
    assert adapted["apps"]["pki"]["certificate_authorities"]["local"]["install_trust"] is False
    issuers = [issuer["module"] for policy in adapted["apps"]["tls"]["automation"]["policies"]
               for issuer in policy.get("issuers", [])]
    assert issuers and set(issuers) == {"internal"}, issuers
    run(*prefix, "validate", "--config", str(CONFIG), "--adapter", "caddyfile")
    OVERRIDE.parent.mkdir(mode=0o755, exist_ok=True)
    OVERRIDE.write_text(override)
    OVERRIDE.chmod(0o644)
    run("systemctl", "daemon-reload")
    run("systemctl", "restart", "rdcontrol-caddy")
    for _ in range(20):
        if ROOT_CERT.is_file():
            break
        time.sleep(0.5)
    else:
        raise SystemExit("Public root certificate was not generated")
    # Only the public certificate is exported. Private keys stay in the service data directory.
    with PUBLIC_EXPORT.open("xb") as output:
        output.write(ROOT_CERT.read_bytes())
    PUBLIC_EXPORT.chmod(0o644)
    print(run("openssl", "x509", "-in", str(PUBLIC_EXPORT), "-noout",
              "-fingerprint", "-sha256", "-subject", "-dates"))
    print("Public PEM SHA256:", hashlib.sha256(PUBLIC_EXPORT.read_bytes()).hexdigest())
    print("Export:", PUBLIC_EXPORT)
    print("System trust installation explicitly disabled. Verify HTTPS with this CA before embedding it.")


if __name__ == "__main__":
    main()
