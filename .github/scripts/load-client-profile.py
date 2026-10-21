#!/usr/bin/env python3
"""Validate public build inputs, then export them for subsequent runner steps."""
import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import ssl
import sys
import time
from urllib.parse import urlsplit

FIELDS = {
    "id_server": "RD_PROFILE_ID_SERVER",
    "relay_server": "RD_PROFILE_RELAY_SERVER",
    "api_server": "RD_PROFILE_API_SERVER",
    "server_public_key": "RD_PROFILE_SERVER_PUBLIC_KEY",
}


def inside(root, value):
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError("Profile and public CA must stay inside the checked-out repository") from error
    return path


def checked_host(host):
    if not host:
        raise ValueError("Server host is missing")
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith((".invalid", ".test", ".example")):
        raise ValueError("A placeholder host cannot be packaged")
    if any(lowered == name or lowered.endswith("." + name)
           for name in ("example.com", "example.net", "example.org")):
        raise ValueError("An example host cannot be packaged")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValueError("Unspecified, loopback and multicast hosts cannot be packaged")


def load(root, profile_file):
    data = json.loads(profile_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Profile must be a JSON object")
    expected = set(FIELDS) | {"ca_pem"}
    if set(data) != expected:
        raise ValueError("Profile must contain exactly the four public settings and ca_pem")
    for name in expected:
        value = data[name]
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(name + " must be explicitly set")
        if any(c in value for c in "\r\n\0<>"):
            raise ValueError(name + " contains a placeholder or invalid control character")
        if name != "ca_pem" and any(c.isspace() for c in value):
            raise ValueError(name + " must not contain whitespace")
    for name in ("id_server", "relay_server"):
        parsed = urlsplit("//" + data[name])
        checked_host(parsed.hostname)
        if "://" in data[name] or parsed.username or parsed.password:
            raise ValueError(name + " must be host[:port], without credentials")
        if parsed.path or parsed.query or parsed.fragment:
            raise ValueError(name + " must not contain a path, query or fragment")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError(name + " port is invalid")
    api = urlsplit(data["api_server"])
    checked_host(api.hostname)
    if api.scheme != "https" or api.username or api.password or api.query or api.fragment:
        raise ValueError("API must be an explicit HTTPS base URL without credentials/query/fragment")
    if api.path.rstrip("/").endswith("/api"):
        raise ValueError("API base URL must not append the client's /api suffix")
    if api.port is not None and not 1 <= api.port <= 65535:
        raise ValueError("API port is invalid")
    public_key = base64.b64decode(data["server_public_key"], validate=True)
    if len(public_key) != 32 or public_key == bytes(32):
        raise ValueError("server_public_key must be the actual 32-byte hbbs public key")

    ca_file = inside(root, data["ca_pem"])
    if ca_file.suffix.lower() not in {".pem", ".crt"}:
        raise ValueError("ca_pem must name a public PEM certificate file")
    pem = ca_file.read_bytes()
    if b"PRIVATE KEY" in pem or pem.count(b"-----BEGIN CERTIFICATE-----") != 1:
        raise ValueError("CA file must contain one public certificate and no private key")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=pem.decode("ascii"))
    certificates = context.get_ca_certs()
    if len(certificates) != 1 or context.cert_store_stats()["x509_ca"] != 1:
        raise ValueError("Public certificate must be a CA certificate")
    now = time.time()
    if not (ssl.cert_time_to_seconds(certificates[0]["notBefore"]) <= now
            < ssl.cert_time_to_seconds(certificates[0]["notAfter"])):
        raise ValueError("Public CA certificate is not currently valid")

    values = {environment: data[field] for field, environment in FIELDS.items()}
    # resolve() uses native Windows paths under python.exe, not Git Bash /d/... paths.
    values["RD_PROFILE_CA_PEM_PATH"] = str(ca_file)
    return values, hashlib.sha256(profile_file.read_bytes()).hexdigest(), hashlib.sha256(pem).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=os.environ.get("GITHUB_WORKSPACE", "."))
    parser.add_argument("--profile", default="res/deployment-profile.json")
    parser.add_argument("--write-github-env", action="store_true")
    args = parser.parse_args()
    try:
        root = Path(args.repo_root).resolve(strict=True)
        profile_file = inside(root, args.profile)
        values, profile_sha, ca_sha = load(root, profile_file)
        if args.write_github_env:
            destination = os.environ.get("GITHUB_ENV")
            if not destination:
                raise ValueError("GITHUB_ENV is missing")
            # Validate everything before writing any build setting.
            with open(destination, "a", encoding="utf-8", newline="\n") as stream:
                for key, value in values.items():
                    stream.write(key + "=" + value + "\n")
        print("Public client profile validated; profile_sha256=" + profile_sha + "; ca_sha256=" + ca_sha)
        return 0
    except (OSError, ValueError, ssl.SSLError) as error:
        print("BUILD BLOCKED: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
