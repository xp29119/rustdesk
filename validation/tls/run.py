#!/usr/bin/env python3
"""Loopback TLS fixtures for the compiled, unchanged production api_tls helper."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time

HOST = "127.0.0.1"


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def command(argv, *, check=True):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=20)
    if check and result.returncode:
        raise AssertionError("fixture OpenSSL command failed: " + Path(argv[0]).name + " " + argv[1])
    return result


def fixtures(root):
    """Use a reviewed sequence of local OpenSSL operations; no download or external CA."""
    openssl = shutil.which("openssl")
    require(openssl is not None, "OpenSSL CLI required")
    authorities = {}
    for name in ("trusted", "other"):
        folder = root / name
        folder.mkdir(mode=0o700)
        (folder / "issued").mkdir(mode=0o700)
        (folder / "index.txt").write_text("")
        (folder / "serial").write_text("1000\n")
        key, cert = folder / "ca-key.pem", folder / "ca.pem"
        command([openssl, "req", "-new", "-x509", "-newkey", "ec", "-pkeyopt",
                 "ec_paramgen_curve:P-256", "-nodes", "-sha256", "-days", "3",
                 "-subj", "/CN=RustDesk synthetic " + name + " authority",
                 "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                 "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                 "-keyout", str(key), "-out", str(cert)])
        config = folder / "issuer.conf"
        config.write_text("\n".join([
            "[ca]", "default_ca=issuer", "[issuer]",
            'database="' + str(folder / "index.txt") + '"',
            'new_certs_dir="' + str(folder / "issued") + '"',
            'certificate="' + str(cert) + '"', 'private_key="' + str(key) + '"',
            'serial="' + str(folder / "serial") + '"', "default_md=sha256",
            "default_days=2", "policy=identity", "unique_subject=no",
            "[identity]", "commonName=supplied", "",
        ]))
        authorities[name] = {"cert": cert, "config": config}
    results = {"ca": authorities["trusted"]["cert"], "other_ca": authorities["other"]["cert"]}
    now = datetime.now(timezone.utc)
    for name, authority, san, expired in (
        ("valid", "trusted", HOST, False), ("wrong_ca", "other", HOST, False),
        ("wrong_ip", "trusted", "127.0.0.2", False), ("expired", "trusted", HOST, True),
    ):
        key, csr, cert = root / (name + "-key.pem"), root / (name + ".csr"), root / (name + ".pem")
        command([openssl, "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                 "-nodes", "-subj", "/CN=RustDesk synthetic " + name,
                 "-keyout", str(key), "-out", str(csr)])
        ext = root / (name + ".ext")
        ext.write_text("[server]\nbasicConstraints=critical,CA:FALSE\n"
                       "keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n"
                       "subjectAltName=IP:" + san + "\n")
        start = now - timedelta(days=2) if expired else now - timedelta(hours=1)
        end = now - timedelta(days=1) if expired else now + timedelta(days=2)
        command([openssl, "ca", "-batch", "-notext", "-config", str(authorities[authority]["config"]),
                 "-in", str(csr), "-out", str(cert), "-extfile", str(ext), "-extensions", "server",
                 "-startdate", start.strftime("%Y%m%d%H%M%SZ"), "-enddate", end.strftime("%Y%m%d%H%M%SZ")])
        results[name] = {"key": key, "cert": cert}
    # Validate fixture meanings independently before using the production helper.
    for name in ("valid", "wrong_ca", "wrong_ip", "expired"):
        result = command([openssl, "verify", "-CAfile", str(results["ca"]), "-verify_ip", HOST,
                          str(results[name]["cert"])], check=False)
        require((result.returncode == 0) == (name == "valid"), "incorrect certificate fixture: " + name)
    return results


class ObservedServer:
    def __init__(self, cert=None, key=None, redirect=None):
        self.context = None
        if cert:
            self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.context.load_cert_chain(cert, key)
        self.redirect = redirect
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((HOST, 0))
        self.sock.listen(8)
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.url = ("https" if self.context else "http") + f"://{HOST}:{self.port}"
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.workers = []
        self.events = {"accepted": 0, "tls_attempts": 0, "handshake_success": 0,
                       "handshake_error": 0, "plaintext_attempts": 0, "http_requests": 0}
        self.requests = []
        self.thread = threading.Thread(target=self.listen, daemon=True)

    def event(self, name):
        with self.lock:
            self.events[name] += 1

    def snapshot(self):
        with self.lock:
            return dict(self.events)

    def listen(self):
        while not self.stopped.is_set():
            try:
                stream, address = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            require(address[0] == HOST, "non-loopback fixture client")
            self.event("accepted")
            worker = threading.Thread(target=self.handle, args=(stream,), daemon=True)
            self.workers.append(worker)
            worker.start()

    def handle(self, stream):
        try:
            stream.settimeout(3)
            first = stream.recv(8, socket.MSG_PEEK)
            if first.startswith(b"\x16"):
                self.event("tls_attempts")
                if not self.context:
                    return
                try:
                    stream = self.context.wrap_socket(stream, server_side=True)
                    self.event("handshake_success")
                except ssl.SSLError:
                    self.event("handshake_error")
                    return
            else:
                self.event("plaintext_attempts")
                if self.context:
                    return
            data = b""
            while b"\r\n\r\n" not in data:
                part = stream.recv(4096)
                if not part:
                    return
                data += part
                require(len(data) <= 16384, "fixture request headers too large")
            head, body = data.split(b"\r\n\r\n", 1)
            content_length = 0
            for line in head.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1])
            require(content_length <= 8192, "fixture request body too large")
            while len(body) < content_length:
                part = stream.recv(content_length - len(body))
                if not part:
                    return
                body += part
            self.event("http_requests")
            with self.lock:
                self.requests.append((head.split(b"\r\n", 1)[0], body))
            if self.redirect:
                response = ("HTTP/1.1 302 Found\r\nLocation: " + self.redirect
                            + "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").encode()
            else:
                payload = b"fixture-accepted"
                response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
                response += str(len(payload)).encode() + b"\r\nConnection: close\r\n\r\n" + payload
            stream.sendall(response)
        except (OSError, ValueError, AssertionError):
            # Assertions in the parent use counters and fail if a complete expected request is absent.
            pass
        finally:
            stream.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stopped.set()
        self.sock.close()
        self.thread.join(timeout=1)
        for worker in self.workers:
            worker.join(timeout=4)
        require(not self.thread.is_alive() and not any(t.is_alive() for t in self.workers),
                "fixture thread did not stop")


def call_helper(binary, source_hash, ca, base, target, mode="request", method="GET", body=None, extra_env=None):
    request = {"mode": mode, "api_base": base, "target": target, "ca_pem": ca.read_text(),
               "method": method, "body": body}
    # Preserve loader/runtime PATH only; never forward the user's proxy or trust environment.
    env = {"PATH": os.defpath, "LANG": "C", "TZ": "UTC"}
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run([str(binary)], input=json.dumps(request), text=True, capture_output=True,
                               timeout=18, env=env)
    require(completed.returncode == 0, "production helper harness process failed")
    result = json.loads(completed.stdout)
    require(result.get("production_helper_sha256") == source_hash, "compiled helper does not match reviewed source")
    require(result.get("real_production_helper") is True and result.get("actualClient") is False,
            "unexpected helper result metadata")
    return result


def integration(binary, source, generated):
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    report = {"real_production_helper": False, "actualClient": False, "real_tls": False,
              "production_helper_sha256": expected_hash, "harness_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "listener_scope": "127.0.0.1 only, ephemeral ports", "tests": [],
              "limits": ["Standalone production TLS helper; full RustDesk binary not executed",
                         "Three outer client call sites require separate static integration review",
                         "Synthetic certificates only; production endpoint not contacted"]}
    ca = generated["ca"]

    def test(name, operation):
        item = {"name": name}
        started = time.monotonic()
        try:
            item["evidence"] = operation()
            item["status"] = "pass"
            report["real_production_helper"] = True
            if name == "valid_private_ca_ip_san_post" or name.endswith("_rejected_without_retry"):
                report["real_tls"] = True
        except Exception as error:
            item.update(status="fail", error=type(error).__name__ + ": " + str(error))
        item["seconds"] = round(time.monotonic() - started, 3)
        report["tests"].append(item)
        print(item["status"].upper(), name, flush=True)

    def valid():
        with ObservedServer(**generated["valid"]) as server:
            classification = call_helper(binary, expected_hash, ca, server.url,
                                         server.url + "/api/login", mode="classify")
            require(classification.get("outcome") == "classified" and classification.get("matched") is True,
                    "exact origin did not select the production private TLS helper")
            body = '{"purpose":"synthetic-tls-fixture"}'
            result = call_helper(binary, expected_hash, ca, server.url, server.url + "/api/login",
                                 method="POST", body=body)
            require(result.get("outcome") == "response" and result.get("status") == 200,
                    "valid certificate did not succeed")
            require(result.get("body") == "fixture-accepted", "valid response body mismatch")
            require(server.requests == [(b"POST /api/login HTTP/1.1", body.encode())],
                    "production helper did not deliver the expected request")
            counters = server.snapshot()
            require(counters["accepted"] == counters["tls_attempts"] == counters["http_requests"] == 1
                    and counters["plaintext_attempts"] == 0, "unexpected successful request transport")
            return counters

    test("valid_private_ca_ip_san_post", valid)

    for name in ("wrong_ca", "wrong_ip", "expired"):
        def reject_certificate(name=name):
            with ObservedServer(**generated[name]) as server:
                # The other CA is also offered through a trust environment variable. It must not
                # enlarge this helper's explicit private-root-only store.
                result = call_helper(binary, expected_hash, ca, server.url, server.url + "/api/login",
                                     extra_env={"SSL_CERT_FILE": str(generated["other_ca"])})
                require(result.get("outcome") == "error" and result.get("phase") == "request",
                        "invalid certificate did not fail in request")
                counters = server.snapshot()
                require(counters["accepted"] == counters["tls_attempts"] == 1,
                        "TLS rejection must have exactly one observed attempt and no reconnect")
                require(counters["http_requests"] == counters["plaintext_attempts"] == 0,
                        "TLS failure was retried as plaintext or reached application HTTP")
                require(any("cert" in s.lower() or "issuer" in s.lower() for s in result.get("errors", [])),
                        "failure was not a certificate verification error")
                return {"observations": counters, "certificate_error": True, "raw_http_retries": 0}
        test(name + "_rejected_without_retry", reject_certificate)

    def origin_case(kind):
        with ExitStack() as stack:
            expected = stack.enter_context(ObservedServer(**generated["valid"]))
            other = stack.enter_context(ObservedServer(**generated["valid"]))
            if kind == "http":
                target = expected.url.replace("https:", "http:") + "/api/login"
            elif kind == "port":
                target = other.url + "/api/login"
            else:
                target = expected.url.replace(HOST, "localhost") + "/api/login"
            classification = call_helper(binary, expected_hash, ca, expected.url, target, mode="classify")
            if kind == "host":
                require(classification.get("outcome") == "classified" and classification.get("matched") is False,
                        "other host should remain outside private trust selection")
            else:
                require(classification.get("outcome") == "error" and classification.get("phase") == "classify",
                        "same API host variant must fail classification instead of reaching fallback")
            result = call_helper(binary, expected_hash, ca, expected.url, target)
            require(result.get("outcome") == "error" and result.get("phase") == "request",
                    "nonmatching origin was not rejected before connecting")
            require(expected.snapshot()["accepted"] == other.snapshot()["accepted"] == 0,
                    "nonmatching origin received a private-trust connection")
            return {"classification": classification["outcome"], "connections": 0}

    for kind in ("http", "port", "host"):
        test("different_" + kind + "_does_not_receive_private_trust", lambda kind=kind: origin_case(kind))

    def redirect():
        with ExitStack() as stack:
            target = stack.enter_context(ObservedServer())
            source_server = stack.enter_context(ObservedServer(**generated["valid"], redirect=target.url + "/trap"))
            result = call_helper(binary, expected_hash, ca, source_server.url, source_server.url + "/api/login")
            require(result.get("outcome") == "response" and result.get("status") == 302,
                    "redirect was followed or suppressed instead of returned")
            require(result.get("url") == source_server.url + "/api/login", "response URL escaped the configured origin")
            require(source_server.snapshot()["http_requests"] == 1 and target.snapshot()["accepted"] == 0,
                    "redirect target was contacted")
            return {"source_requests": 1, "redirect_status": 302, "target_connections": 0}

    test("redirect_not_followed_and_no_http_downgrade", redirect)

    def environment_proxy():
        with ExitStack() as stack:
            proxy = stack.enter_context(ObservedServer())
            server = stack.enter_context(ObservedServer(**generated["valid"]))
            result = call_helper(binary, expected_hash, ca, server.url, server.url + "/api/login",
                                 extra_env={"HTTPS_PROXY": proxy.url, "https_proxy": proxy.url,
                                            "ALL_PROXY": proxy.url, "NO_PROXY": ""})
            require(result.get("outcome") == "response" and result.get("status") == 200,
                    "environment proxy changed configured direct request")
            require(proxy.snapshot()["accepted"] == 0 and server.snapshot()["http_requests"] == 1,
                    "environment proxy received private API traffic")
            return {"direct_requests": 1, "proxy_connections": 0}

    test("environment_proxy_not_used", environment_proxy)
    report["passed"] = all(item["status"] == "pass" for item in report["tests"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, help="already-built small Rust validation executable")
    parser.add_argument("--source", type=Path, help="exact reviewed production src/api_tls.rs")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--fixtures-only", action="store_true", help="check synthetic certificates only; no helper claim")
    args = parser.parse_args()
    os.umask(0o077)
    report = {"passed": False, "real_production_helper": False, "actualClient": False}
    try:
        with tempfile.TemporaryDirectory(prefix="rustdesk-tls-fixtures-") as directory:
            generated = fixtures(Path(directory))
            if args.fixtures_only:
                report.update(passed=True, real_tls=False, kind="OpenSSL fixture self-check",
                              valid_and_three_invalid_certificate_cases_verified=True)
            else:
                require(args.binary and args.source, "--binary and --source required")
                binary, source = args.binary.resolve(strict=True), args.source.resolve(strict=True)
                require(binary.is_file() and os.access(binary, os.X_OK), "harness binary must be executable")
                report = integration(binary, source, generated)
        report["temporary_keys_removed"] = True
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("passed=" + str(report["passed"]).lower()
          + "; real_production_helper=" + str(report["real_production_helper"]).lower()
          + "; actualClient=false", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
