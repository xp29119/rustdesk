#!/usr/bin/env python3
"""Configure only the administrator origin/cookie namespace; keep client IP identity."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
import urllib.request

MARKER = "# Managed by RDControl portal adapter\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", args.origin):
        raise SystemExit("Expected a plain HTTPS origin, without paths or credentials")
    parsed = urlsplit(args.origin)
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise SystemExit("Invalid HTTPS port")
    contents = MARKER + (
        "[Service]\nExecStart=\n"
        "ExecStart=/usr/bin/python3 -m rdcontrol --data-dir /var/lib/rdcontrol "
        "--public-url https://8.138.230.183 --port 8088 "
        f"--admin-origin {args.origin} --admin-cookie-path /apps/rustdesk/ "
        "--admin-cookie-name rdcontrol_admin\n"
    )
    if not args.apply:
        print(contents)
        print("Dry run only; no service change.")
        return
    if os.geteuid() != 0 or os.uname().sysname != "Linux":
        raise SystemExit("Apply as root on ali")
    with urllib.request.urlopen("http://127.0.0.1:8088/api/meta", timeout=3) as response:
        meta = json.load(response)
    if meta["version"] not in ("0.1.2", "0.1.3", "0.1.4"):
        raise SystemExit("Install and verify a supported backend first")
    folder = Path("/etc/systemd/system/rdcontrol.service.d")
    folder.mkdir(mode=0o755, exist_ok=True)
    target = folder / "portal.conf"
    if target.exists():
        old = target.read_text()
        if not old.startswith(MARKER):
            raise SystemExit("Refusing to replace an unrelated service override")
        if old == contents:
            print("Matching administrator configuration is already present.")
            return
        target.with_suffix(f".before-{int(time.time())}").write_text(old)
    target.write_text(contents)
    target.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "restart", "rdcontrol"], check=True)
    print("Administrator origin and cookie namespace configured. Client issuer remains the IP URL.")


if __name__ == "__main__":
    main()
