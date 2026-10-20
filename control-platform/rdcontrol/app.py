import asyncio
import base64
from collections import defaultdict, deque
from contextlib import suppress
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
import uuid

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
import jwt

from .store import Store

STATIC = Path(__file__).parent / "static"
ACCESS_SECONDS = 300
SESSION_SECONDS = 30 * 24 * 3600
ADMIN_SECONDS = 8 * 3600
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")


def now():
    return int(time.time())


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def b64url(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise web.HTTPBadRequest(text="Invalid device public key")
    raw = base64.urlsafe_b64decode(value + "=")
    if len(raw) != 32:
        raise web.HTTPBadRequest(text="Invalid device public key")
    return Ed25519PublicKey.from_public_bytes(raw)


def require(condition, message="Access denied", status=403):
    if not condition:
        raise {
            400: web.HTTPBadRequest,
            401: web.HTTPUnauthorized,
            403: web.HTTPForbidden,
            409: web.HTTPConflict,
            429: web.HTTPTooManyRequests,
        }[status](text=message)


class Control:
    def __init__(self, data_dir, public_url, id_upstream, relay_upstream, *,
                 admin_origin=None, admin_cookie_path="/", admin_cookie_name="control_session"):
        self.public_url = public_url.rstrip("/")
        url = urlsplit(self.public_url)
        require(
            url.scheme == "https"
            or (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost")),
            "HTTPS is required outside loopback", 400,
        )
        require(not url.path and not url.query and not url.username, "Invalid public URL", 400)
        self.admin_origin = (admin_origin or self.public_url).rstrip("/")
        admin_url = urlsplit(self.admin_origin)
        require(
            (admin_url.scheme == "https" or
             (admin_url.scheme == "http" and admin_url.hostname in ("127.0.0.1", "localhost")))
            and bool(admin_url.hostname) and not admin_url.username and not admin_url.password
            and not admin_url.path and not admin_url.query and not admin_url.fragment,
            "Invalid administration origin", 400,
        )
        require(re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*", admin_cookie_path),
                "Invalid administrator cookie path", 400)
        self.admin_cookie_path = admin_cookie_path
        require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", admin_cookie_name),
                "Invalid administrator cookie name", 400)
        self.admin_cookie_name = admin_cookie_name
        self.secure = admin_url.scheme == "https"
        data_dir = Path(data_dir)
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.store = Store(data_dir / "control.sqlite3")
        key_path = data_dir / "issuer-key.pem"
        if not key_path.exists():
            key = Ed25519PrivateKey.generate()
            data = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            with key_path.open("xb") as file:
                os.chmod(key_path, 0o600)
                file.write(data)
        self.signing_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        self.bootstrap_path = data_dir / "bootstrap-token.txt"
        if not self.has_admin() and not self.bootstrap_path.exists():
            with self.bootstrap_path.open("x") as file:
                os.chmod(self.bootstrap_path, 0o600)
                file.write(secrets.token_urlsafe(32))
        self.hasher = PasswordHasher(time_cost=3, memory_cost=32768, parallelism=2)
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(24))
        self.hash_slots = asyncio.Semaphore(2)
        self.attempts = defaultdict(deque)
        self.websockets = {}
        self.pending_opens = defaultdict(int)
        self.http = None
        self.upstreams = {"id": id_upstream, "relay": relay_upstream}
        for endpoint in self.upstreams.values():
            parsed = urlsplit(endpoint)
            require(
                parsed.scheme == "ws" and parsed.hostname in ("127.0.0.1", "::1"),
                "RustDesk upstream must be loopback", 400,
            )

    def has_admin(self):
        return self.store.one("SELECT id FROM users WHERE role='admin'") is not None

    def ip(self, request):
        # Only Caddy on loopback may supply the sanitized real client address.
        if request.remote in ("127.0.0.1", "::1"):
            return request.headers.get("X-Real-IP", request.remote)
        return request.remote or ""

    def audit(self, event, request=None, actor=None, device=None, **details):
        self.store.write(
            "INSERT INTO audit(time,actor,device_id,event,ip,details) VALUES(?,?,?,?,?,?)",
            (now(), actor, device, event, self.ip(request) if request else "",
             json.dumps(details, ensure_ascii=False)),
        )

    def rate(self, bucket, limit=8, seconds=60):
        t = time.monotonic()
        # Bounded even when unsolicited requests use many spoofed usernames.
        if len(self.attempts) > 4096:
            self.attempts = defaultdict(
                deque,
                {k: v for k, v in self.attempts.items() if v and v[-1] > t - 300},
            )
            require(len(self.attempts) <= 4096, "Try again later", 429)
        q = self.attempts[bucket]
        while q and q[0] <= t - seconds:
            q.popleft()
        require(len(q) < limit, "Too many attempts; try again later", 429)
        q.append(t)

    async def body(self, request):
        require(request.content_type == "application/json", "JSON required", 400)
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="Invalid JSON")
        require(isinstance(data, dict), "JSON object required", 400)
        return data

    def web_origin(self, request):
        require(request.headers.get("Origin") == self.admin_origin, "Invalid request origin")

    async def verify_password(self, request, username, password, admin=False):
        self.rate("login:" + self.ip(request))
        require(isinstance(username, str) and isinstance(password, str), "Invalid credentials", 401)
        require(len(password) <= 256 and len(username) <= 40, "Invalid credentials", 401)
        user = self.store.one("SELECT * FROM users WHERE username=?", (username,))
        async with self.hash_slots:
            try:
                valid = await asyncio.to_thread(
                    self.hasher.verify, user["password_hash"] if user else self.dummy_hash, password
                )
            except (VerificationError, InvalidHashError):
                valid = False
        if not valid or not user or not user["enabled"] or (admin and user["role"] != "admin"):
            self.audit("login_failed", request)
            raise web.HTTPUnauthorized(text="Invalid username or password")
        return user

    async def password_hash(self, password):
        require(isinstance(password, str) and 12 <= len(password) <= 256,
                "Password must contain 12–256 characters", 400)
        async with self.hash_slots:
            return await asyncio.to_thread(self.hasher.hash, password)

    def proof(self, request, public_key, token=None):
        raw = request.headers.get("DPoP", "")
        require(0 < len(raw) < 4096, "Device proof required", 401)
        try:
            header = jwt.get_unverified_header(raw)
            jwk = header.get("jwk", {})
            require(header.get("typ") == "dpop+jwt" and header.get("alg") == "EdDSA",
                    "Invalid proof", 401)
            require(jwk == {"kty": "OKP", "crv": "Ed25519", "x": public_key},
                    "Device key mismatch", 401)
            payload = jwt.decode(
                raw, decode_key(public_key), algorithms=["EdDSA"],
                options={"require": ["jti", "iat", "htm", "htu"], "verify_aud": False},
                leeway=5,
            )
            require(isinstance(payload["iat"], int) and abs(now() - payload["iat"]) <= 30,
                    "Expired device proof", 401)
            require(payload["htm"] == request.method, "Proof method mismatch", 401)
            require(payload["htu"] == self.public_url + request.path,
                    "Proof address mismatch", 401)
            jti = payload["jti"]
            require(isinstance(jti, str) and 16 <= len(jti) <= 128, "Invalid proof id", 401)
            if token is not None:
                require(hmac.compare_digest(payload.get("ath", ""),
                                            b64url(hashlib.sha256(token.encode()).digest())),
                        "Proof token mismatch", 401)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise web.HTTPUnauthorized(text="Invalid device proof")
        self.store.write("DELETE FROM proofs WHERE expires < ?", (now(),))
        try:
            self.store.write("INSERT INTO proofs VALUES(?,?)",
                             (digest(public_key + ":" + jti), now() + 65))
        except sqlite3.IntegrityError:
            raise web.HTTPUnauthorized(text="Replayed device proof")

    def live_session(self, session_id):
        return self.store.one(
            """SELECT s.*,d.status,d.public_key,d.name,u.enabled,u.role
            FROM sessions s JOIN devices d ON d.id=s.device_id JOIN users u ON u.id=s.user_id
            WHERE s.id=? AND s.revoked=0 AND s.expires>? AND u.enabled=1""",
            (session_id, now()),
        )

    def authorize(self, request):
        self.rate("access:" + self.ip(request), limit=300)
        auth = request.headers.get("Authorization", "")
        require(auth.startswith("Bearer ") and len(auth) < 4096, "Login required", 401)
        token = auth[7:]
        try:
            claims = jwt.decode(
                token, self.signing_key.public_key(), algorithms=["EdDSA"],
                audience="rustdesk-gateway", issuer=self.public_url,
                options={"require": ["exp", "iat", "sid", "sub", "did", "cnf"]},
            )
        except jwt.PyJWTError:
            raise web.HTTPUnauthorized(text="Login expired")
        session = self.live_session(claims["sid"])
        require(session is not None, "Session revoked or expired", 401)
        require(session["status"] == "approved", "Device approval required")
        require(claims["sub"] == session["user_id"] and claims["did"] == session["device_id"],
                "Invalid session", 401)
        require(claims["cnf"] == {"jkt": self.thumbprint(session["public_key"])},
                "Invalid token binding", 401)
        self.proof(request, session["public_key"], token)
        return session

    @staticmethod
    def thumbprint(public_key):
        jwk = {"crv": "Ed25519", "kty": "OKP", "x": public_key}
        return b64url(hashlib.sha256(json.dumps(jwk, separators=(",", ":"), sort_keys=True).encode()).digest())

    def credentials(self, session, refresh):
        result = {
            "device_id": session["device_id"], "device_status": session["status"],
            "refresh_token": refresh, "expires_in": ACCESS_SECONDS,
        }
        if session["status"] == "approved":
            t = now()
            result["access_token"] = jwt.encode(
                {"iss": self.public_url, "aud": "rustdesk-gateway", "iat": t,
                 "exp": t + ACCESS_SECONDS, "sub": session["user_id"], "sid": session["id"],
                 "did": session["device_id"],
                 "cnf": {"jkt": self.thumbprint(session["public_key"])}},
                self.signing_key, algorithm="EdDSA",
            )
        return result

    def admin(self, request, mutate=False):
        token = request.cookies.get(self.admin_cookie_name, "")
        require(0 < len(token) <= 128, "Admin login required", 401)
        session = self.store.one(
            """SELECT a.*,u.username FROM admin_sessions a JOIN users u ON u.id=a.user_id
            WHERE a.hash=? AND a.expires>? AND u.enabled=1 AND u.role='admin'""",
            (digest(token), now()),
        )
        require(session is not None, "Admin login required", 401)
        if mutate:
            self.web_origin(request)
            require(hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session["csrf"]),
                    "Invalid CSRF token")
        return session

    async def meta(self, request):
        return web.json_response({"setup_required": not self.has_admin(), "version": "0.1.2"})

    async def setup(self, request):
        self.web_origin(request)
        self.rate("setup:" + self.ip(request), limit=5)
        require(not self.has_admin(), "Setup already completed", 409)
        body = await self.body(request)
        expected = self.bootstrap_path.read_text().strip()
        require(isinstance(body.get("token"), str) and
                hmac.compare_digest(body["token"], expected), "Invalid setup code")
        username = body.get("username", "")
        require(isinstance(username, str) and USERNAME.fullmatch(username), "Invalid username", 400)
        hashed = await self.password_hash(body.get("password"))
        uid = str(uuid.uuid4())
        # No await between the second check and insert.
        require(not self.has_admin(), "Setup already completed", 409)
        self.store.write("INSERT INTO users VALUES(?,?,?,?,?,?)",
                         (uid, username, hashed, "admin", 1, now()))
        self.bootstrap_path.unlink(missing_ok=True)
        self.audit("platform_initialized", request, actor=uid)
        return web.json_response({"ok": True}, status=201)

    async def admin_login(self, request):
        self.web_origin(request)
        body = await self.body(request)
        user = await self.verify_password(request, body.get("username"), body.get("password"), True)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self.store.write("INSERT INTO admin_sessions VALUES(?,?,?,?)",
                         (digest(token), user["id"], csrf, now() + ADMIN_SECONDS))
        self.audit("admin_login", request, actor=user["id"])
        response = web.json_response({"username": user["username"], "csrf": csrf})
        response.set_cookie(self.admin_cookie_name, token, max_age=ADMIN_SECONDS,
                            httponly=True, secure=self.secure, samesite="Strict",
                            path=self.admin_cookie_path)
        return response

    async def admin_session(self, request):
        session = self.admin(request)
        return web.json_response({"username": session["username"], "csrf": session["csrf"]})

    async def admin_logout(self, request):
        session = self.admin(request, True)
        self.store.write("DELETE FROM admin_sessions WHERE hash=?", (session["hash"],))
        response = web.json_response({"ok": True})
        response.del_cookie(self.admin_cookie_name, path=self.admin_cookie_path)
        return response

    async def overview(self, request):
        self.admin(request)
        return web.json_response({
            "users": self.store.all("SELECT id,username,role,enabled,created FROM users ORDER BY created"),
            "devices": self.store.all(
                """SELECT d.id,d.name,d.platform,d.status,d.created,d.last_seen,d.last_ip,u.username
                FROM devices d JOIN users u ON u.id=d.user_id ORDER BY d.created DESC"""),
            "audit": self.store.all(
                """SELECT a.*,u.username FROM audit a LEFT JOIN users u ON u.id=a.actor
                ORDER BY a.id DESC LIMIT 100"""),
            "connections": self.store.all(
                """SELECT c.*,d.name,u.username FROM connections c
                JOIN devices d ON d.id=c.device_id JOIN users u ON u.id=d.user_id
                ORDER BY c.started DESC LIMIT 100"""),
            "active_transports": len(self.websockets),
        })

    async def create_user(self, request):
        admin = self.admin(request, True)
        body = await self.body(request)
        username = body.get("username", "")
        require(isinstance(username, str) and USERNAME.fullmatch(username), "Invalid username", 400)
        hashed = await self.password_hash(body.get("password"))
        uid = str(uuid.uuid4())
        try:
            self.store.write("INSERT INTO users VALUES(?,?,?,?,?,?)",
                             (uid, username, hashed, "user", 1, now()))
        except sqlite3.IntegrityError:
            raise web.HTTPConflict(text="Username already exists")
        self.audit("user_created", request, actor=admin["user_id"], target=uid)
        return web.json_response({"id": uid}, status=201)

    async def user_status(self, request):
        admin = self.admin(request, True)
        body = await self.body(request)
        uid = request.match_info["id"]
        user = self.store.one("SELECT * FROM users WHERE id=?", (uid,))
        require(user is not None, "Unknown user", 400)
        require(user["role"] != "admin", "Administrator cannot be disabled here")
        require(type(body.get("enabled")) is bool, "Boolean enabled required", 400)
        self.store.write("UPDATE users SET enabled=? WHERE id=?", (int(body["enabled"]), uid))
        if not body["enabled"]:
            self.store.write("UPDATE sessions SET revoked=1 WHERE user_id=?", (uid,))
        self.audit("user_enabled" if body["enabled"] else "user_disabled",
                   request, actor=admin["user_id"], target=uid)
        return web.json_response({"ok": True})

    async def device_decision(self, request):
        admin = self.admin(request, True)
        body = await self.body(request)
        did, decision = request.match_info["id"], body.get("status")
        require(decision in ("approved", "revoked"), "Invalid decision", 400)
        device = self.store.one("SELECT * FROM devices WHERE id=?", (did,))
        require(device is not None, "Unknown device", 400)
        self.store.write("UPDATE devices SET status=? WHERE id=?", (decision, did))
        if decision == "revoked":
            self.store.write("UPDATE sessions SET revoked=1 WHERE device_id=?", (did,))
        self.audit("device_" + decision, request, actor=admin["user_id"], device=did)
        return web.json_response({"ok": True})

    async def device_login(self, request):
        body = await self.body(request)
        user = await self.verify_password(request, body.get("username"), body.get("password"))
        public_key = body.get("public_key")
        decode_key(public_key)
        self.proof(request, public_key)
        name, platform = body.get("device_name", ""), body.get("platform", "")
        require(isinstance(name, str) and 1 <= len(name.strip()) <= 80, "Invalid device name", 400)
        require(platform in ("windows", "macos", "android", "test"), "Invalid platform", 400)
        device = self.store.one("SELECT * FROM devices WHERE public_key=?", (public_key,))
        if device:
            require(device["user_id"] == user["id"], "Device belongs to another account")
            require(device["status"] != "revoked", "Device has been revoked")
        else:
            require(self.store.one("SELECT count(*) AS n FROM devices WHERE user_id=?",
                                   (user["id"],))["n"] < 100, "Device limit reached")
            did = str(uuid.uuid4())
            self.store.write(
                "INSERT INTO devices VALUES(?,?,?,?,?,?,?,?,?)",
                (did, user["id"], name.strip(), platform, public_key, "pending", now(), now(), self.ip(request)),
            )
            device = self.store.one("SELECT * FROM devices WHERE id=?", (did,))
            self.audit("device_enrolled", request, actor=user["id"], device=did)
        sid, refresh = str(uuid.uuid4()), secrets.token_urlsafe(48)
        self.store.write("INSERT INTO sessions VALUES(?,?,?,?,?,0)",
                         (sid, user["id"], device["id"], now(), now() + SESSION_SECONDS))
        self.store.write("INSERT INTO refresh_tokens VALUES(?,?,0)", (digest(refresh), sid))
        self.audit("device_login", request, actor=user["id"], device=device["id"])
        session = self.live_session(sid)
        return web.json_response(self.credentials(session, refresh),
                                 status=200 if device["status"] == "approved" else 202)

    async def refresh(self, request):
        self.rate("refresh:" + self.ip(request), limit=120)
        body = await self.body(request)
        token = body.get("refresh_token", "")
        require(isinstance(token, str) and 40 <= len(token) <= 128, "Invalid refresh token", 401)
        stored = self.store.one("SELECT * FROM refresh_tokens WHERE hash=?", (digest(token),))
        require(stored is not None, "Invalid refresh token", 401)
        session = self.live_session(stored["session_id"])
        require(session is not None, "Session expired or revoked", 401)
        self.proof(request, session["public_key"], token)
        if stored["used"]:
            self.store.write("UPDATE sessions SET revoked=1 WHERE id=?", (session["id"],))
            self.audit("refresh_reuse_detected", request,
                       actor=session["user_id"], device=session["device_id"])
            raise web.HTTPUnauthorized(text="Session revoked after refresh token reuse")
        require(session["status"] != "revoked", "Device revoked", 401)
        replacement = secrets.token_urlsafe(48)
        with self.store.db:
            self.store.db.execute("UPDATE refresh_tokens SET used=1 WHERE hash=?", (digest(token),))
            self.store.db.execute("INSERT INTO refresh_tokens VALUES(?,?,0)",
                                  (digest(replacement), session["id"]))
            self.store.db.execute("UPDATE sessions SET expires=? WHERE id=?",
                                  (now() + SESSION_SECONDS, session["id"]))
            self.store.db.execute("UPDATE devices SET last_seen=?,last_ip=? WHERE id=?",
                                  (now(), self.ip(request), session["device_id"]))
        return web.json_response(self.credentials(session, replacement),
                                 status=200 if session["status"] == "approved" else 202)

    async def device_logout(self, request):
        if request.headers.get("Authorization"):
            session = self.authorize(request)
        else:
            # Pending devices have no access token, but must still be able to log out.
            self.rate("logout:" + self.ip(request), limit=120)
            body = await self.body(request)
            token = body.get("refresh_token", "")
            require(isinstance(token, str) and 40 <= len(token) <= 128,
                    "Invalid refresh token", 401)
            stored = self.store.one("SELECT * FROM refresh_tokens WHERE hash=?", (digest(token),))
            require(stored is not None, "Invalid refresh token", 401)
            session = self.live_session(stored["session_id"])
            require(session is not None, "Session expired or revoked", 401)
            self.proof(request, session["public_key"], token)
        self.store.write("UPDATE sessions SET revoked=1 WHERE id=?", (session["id"],))
        self.audit("device_logout", request, actor=session["user_id"], device=session["device_id"])
        return web.json_response({"ok": True})

    async def me(self, request):
        session = self.authorize(request)
        return web.json_response({"device_id": session["device_id"], "status": session["status"]})

    async def gateway(self, request):
        session = self.authorize(request)
        kind = request.match_info["kind"]
        require(kind in self.upstreams, "Unknown service", 400)
        require(len(self.websockets) + sum(self.pending_opens.values()) < 64,
                "Gateway capacity reached", 429)
        count = sum(x[1] == session["device_id"] for x in self.websockets.values())
        require(count + self.pending_opens[session["device_id"]] < 12,
                "Device connection limit reached", 429)
        cid = str(uuid.uuid4())
        # Only trusted, sanitized IP headers go to the loopback RustDesk websocket listener.
        self.pending_opens[session["device_id"]] += 1
        upstream = None
        try:
            upstream = await self.http.ws_connect(
                self.upstreams[kind], headers={"X-Real-IP": self.ip(request)},
                max_msg_size=64 * 1024 * 1024, heartbeat=25,
            )
            live = self.live_session(session["id"])
            require(live and live["status"] == "approved", "Authorization revoked", 401)
            client = web.WebSocketResponse(max_msg_size=64 * 1024 * 1024, heartbeat=25)
            await client.prepare(request)
            self.websockets[cid] = (session["id"], session["device_id"], client)
        except (ClientError, OSError, asyncio.TimeoutError) as exc:
            if upstream:
                await upstream.close()
            self.audit("upstream_unavailable", request, actor=session["user_id"], device=session["device_id"])
            raise web.HTTPServiceUnavailable(text="RustDesk backend unavailable") from exc
        except BaseException:
            if upstream:
                await upstream.close()
            raise
        finally:
            self.pending_opens[session["device_id"]] -= 1
            if not self.pending_opens[session["device_id"]]:
                del self.pending_opens[session["device_id"]]
        self.store.write("INSERT INTO connections VALUES(?,?,?,?,?,NULL,0,0,'open')",
                         (cid, session["id"], session["device_id"], kind, now()))
        counters = [0, 0]

        async def pump(source, target, index):
            async for msg in source:
                if msg.type == WSMsgType.BINARY:
                    counters[index] += len(msg.data)
                    await target.send_bytes(msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                    break
                else:
                    # RustDesk transport is binary; do not forward arbitrary HTTP/text payloads.
                    await client.close(code=1003, message=b"Binary transport required")
                    break

        tasks = [asyncio.create_task(pump(client, upstream, 0)),
                 asyncio.create_task(pump(upstream, client, 1))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await upstream.close()
            await client.close()
            self.websockets.pop(cid, None)
            self.store.write(
                "UPDATE connections SET ended=?,bytes_up=?,bytes_down=?,outcome='transport_closed' WHERE id=?",
                (now(), *counters, cid),
            )
        return client

    async def revocation_watch(self):
        while True:
            await asyncio.sleep(2)
            for sid, _, socket in list(self.websockets.values()):
                live = self.live_session(sid)
                if not live or live["status"] != "approved":
                    await socket.close(code=1008, message=b"Authorization revoked")

    async def lifecycle(self, app):
        self.http = ClientSession(timeout=ClientTimeout(total=None, sock_connect=8))
        watcher = asyncio.create_task(self.revocation_watch())
        yield
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        for _, _, socket in list(self.websockets.values()):
            await socket.close(code=1001, message=b"Service stopping")
        await self.http.close()
        self.store.db.close()


@web.middleware
async def security(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = web.json_response({"error": exc.text or exc.reason}, status=exc.status)
    if not response.prepared:
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
    return response


def make_app(data_dir, public_url, id_upstream="ws://127.0.0.1:21118",
             relay_upstream="ws://127.0.0.1:21119", *,
             admin_origin=None, admin_cookie_path="/", admin_cookie_name="control_session"):
    control = Control(data_dir, public_url, id_upstream, relay_upstream,
                      admin_origin=admin_origin, admin_cookie_path=admin_cookie_path,
                      admin_cookie_name=admin_cookie_name)
    app = web.Application(middlewares=[security], client_max_size=16384)
    app["control"] = control
    app.cleanup_ctx.append(control.lifecycle)
    for method, path, fn in [
        ("GET", "/api/meta", control.meta),
        ("POST", "/api/setup", control.setup),
        ("POST", "/api/admin/login", control.admin_login),
        ("GET", "/api/admin/session", control.admin_session),
        ("POST", "/api/admin/logout", control.admin_logout),
        ("GET", "/api/admin/overview", control.overview),
        ("POST", "/api/admin/users", control.create_user),
        ("POST", "/api/admin/users/{id}/status", control.user_status),
        ("POST", "/api/admin/devices/{id}/decision", control.device_decision),
        ("POST", "/api/auth/login", control.device_login),
        ("POST", "/api/auth/refresh", control.refresh),
        ("POST", "/api/auth/logout", control.device_logout),
        ("GET", "/api/me", control.me),
        ("GET", "/ws/{kind}", control.gateway),
    ]:
        app.router.add_route(method, path, fn)
    async def index(request):
        return web.FileResponse(STATIC / "index.html")
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app
