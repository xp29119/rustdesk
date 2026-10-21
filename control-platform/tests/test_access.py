import asyncio
import hashlib
import time
import uuid

from aiohttp import web, WSMsgType, WSServerHandshakeError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt
import pytest

from rdcontrol.app import make_app, b64url

ORIGIN = "http://127.0.0.1:8765"
PASSWORD = "A-long-test-password-2026"


def public(key):
    return b64url(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def proof(key, method, path, token=None, **overrides):
    claims = {"jti": str(uuid.uuid4()), "iat": int(time.time()),
              "htm": method, "htu": ORIGIN + path}
    if token is not None:
        claims["ath"] = b64url(hashlib.sha256(token.encode()).digest())
    claims.update(overrides)
    return jwt.encode(
        claims, key, algorithm="EdDSA",
        headers={"typ": "dpop+jwt", "jwk": {"kty": "OKP", "crv": "Ed25519", "x": public(key)}},
    )


def headers(key, token, method="GET", path="/api/me"):
    return {"Authorization": "Bearer " + token, "DPoP": proof(key, method, path, token)}


@pytest.fixture
async def env(aiohttp_client, tmp_path):
    async def echo(request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        async for msg in socket:
            if msg.type == WSMsgType.BINARY:
                await socket.send_bytes(msg.data)
        return socket
    backend = web.Application()
    backend.router.add_get("/", echo)
    upstream = await aiohttp_client(backend)
    upstream_url = str(upstream.make_url("/")).replace("http:", "ws:")
    app = make_app(tmp_path, ORIGIN, upstream_url, upstream_url)
    client = await aiohttp_client(app)
    setup = await client.post("/api/setup", headers={"Origin": ORIGIN}, json={
        "token": (tmp_path / "bootstrap-token.txt").read_text(),
        "username": "operator", "password": PASSWORD,
    })
    assert setup.status == 201, await setup.text()
    res = await client.post("/api/admin/login", headers={"Origin": ORIGIN}, json={
        "username": "operator", "password": PASSWORD,
    })
    assert res.status == 200
    data = await res.json()
    admin = {"Origin": ORIGIN, "X-CSRF-Token": data["csrf"]}
    # Explicit cookie works independently of test CookieJar IP restrictions.
    admin["Cookie"] = "control_session=" + res.cookies["control_session"].value
    return client, admin, app["control"]


async def enroll(env, key=None, username="operator", password=PASSWORD):
    client, _, _ = env
    key = key or Ed25519PrivateKey.generate()
    response = await client.post("/api/auth/login", headers={
        "DPoP": proof(key, "POST", "/api/auth/login")}, json={
        "username": username, "password": password,
        "device_name": "Test desktop", "platform": "test", "public_key": public(key),
    })
    assert response.status == 202, await response.text()
    body = await response.json()
    assert "access_token" not in body
    return key, body


async def approve(env, key, body):
    client, admin, _ = env
    result = await client.post(f"/api/admin/devices/{body['device_id']}/decision",
                              headers=admin, json={"status": "approved"})
    assert result.status == 200, await result.text()
    result = await client.post("/api/auth/refresh", headers={
        "DPoP": proof(key, "POST", "/api/auth/refresh", body["refresh_token"])},
        json={"refresh_token": body["refresh_token"]})
    assert result.status == 200, await result.text()
    return await result.json()


async def test_pending_device_can_revoke_its_own_session(env):
    client, _, _ = env
    key, pending = await enroll(env)
    token = pending["refresh_token"]
    wrong = await client.post("/api/auth/logout", headers={
        "DPoP": proof(Ed25519PrivateKey.generate(), "POST", "/api/auth/logout", token)},
        json={"refresh_token": token})
    assert wrong.status == 401
    result = await client.post("/api/auth/logout", headers={
        "DPoP": proof(key, "POST", "/api/auth/logout", token)}, json={"refresh_token": token})
    assert result.status == 200
    result = await client.post("/api/auth/refresh", headers={
        "DPoP": proof(key, "POST", "/api/auth/refresh", token)}, json={"refresh_token": token})
    assert result.status == 401


async def test_separate_portal_origin_and_cookie_path_do_not_change_device_proof_origin(
        aiohttp_client, tmp_path):
    public_origin = "https://8.138.230.183"
    portal_origin = "https://portal.example.test:18443"
    app = make_app(tmp_path, public_origin, admin_origin=portal_origin,
                   admin_cookie_path="/rustdesk/", admin_cookie_name="rdcontrol_admin")
    client = await aiohttp_client(app)
    setup_body = {"token": (tmp_path / "bootstrap-token.txt").read_text(),
                  "username": "operator", "password": PASSWORD}
    denied = await client.post("/api/setup", headers={"Origin": public_origin}, json=setup_body)
    assert denied.status == 403
    setup = await client.post("/api/setup", headers={"Origin": portal_origin}, json=setup_body)
    assert setup.status == 201
    login_body = {"username": "operator", "password": PASSWORD}
    denied = await client.post("/api/admin/login", headers={"Origin": public_origin}, json=login_body)
    assert denied.status == 403
    login = await client.post("/api/admin/login", headers={"Origin": portal_origin}, json=login_body)
    assert login.status == 200
    cookie = login.cookies["rdcontrol_admin"]
    assert cookie["path"] == "/rustdesk/"
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
    key = Ed25519PrivateKey.generate()
    body = {**login_body, "device_name": "Test client", "platform": "test", "public_key": public(key)}
    denied = await client.post("/api/auth/login", headers={
        "DPoP": proof(key, "POST", "/api/auth/login", htu=portal_origin + "/api/auth/login")},
        json=body)
    assert denied.status == 401
    pending = await client.post("/api/auth/login", headers={
        "DPoP": proof(key, "POST", "/api/auth/login", htu=public_origin + "/api/auth/login")},
        json=body)
    assert pending.status == 202
    csrf = (await login.json())["csrf"]
    logout = await client.post("/api/admin/logout", headers={
        "Origin": portal_origin, "X-CSRF-Token": csrf,
        "Cookie": "control_session=unrelated-existing-cookie; rdcontrol_admin=" + cookie.value}, json={})
    assert logout.status == 200
    assert logout.cookies["rdcontrol_admin"]["path"] == "/rustdesk/"


async def test_approval_is_required_and_revocation_blocks_tokens(env):
    client, admin, _ = env
    key, pending = await enroll(env)
    for path in ("/api/me", "/ws/id", "/ws/relay"):
        res = await client.get(path)
        assert res.status == 401
    refresh = await client.post("/api/auth/refresh", headers={
        "DPoP": proof(key, "POST", "/api/auth/refresh", pending["refresh_token"])},
        json={"refresh_token": pending["refresh_token"]})
    assert refresh.status == 202
    token = await approve(env, key, await refresh.json())
    assert (await client.get("/api/me", headers=headers(key, token["access_token"]))).status == 200
    res = await client.post(f"/api/admin/devices/{pending['device_id']}/decision",
                            headers=admin, json={"status": "revoked"})
    assert res.status == 200
    assert (await client.get("/api/me", headers=headers(key, token["access_token"]))).status == 401
    relogin = await client.post("/api/auth/login", headers={
        "DPoP": proof(key, "POST", "/api/auth/login")}, json={
        "username":"operator","password":PASSWORD,"device_name":"Renamed",
        "platform":"test","public_key":public(key)})
    assert relogin.status == 403


async def test_device_proof_binding_replay_and_forgery(env):
    client, _, control = env
    key, pending = await enroll(env)
    token = (await approve(env, key, pending))["access_token"]
    other = Ed25519PrivateKey.generate()
    assert (await client.get("/api/me", headers=headers(other, token))).status == 401
    h = headers(key, token)
    assert (await client.get("/api/me", headers=h)).status == 200
    assert (await client.get("/api/me", headers=h)).status == 401
    stale = {"Authorization":"Bearer "+token,
             "DPoP":proof(key,"GET","/api/me",token,iat=int(time.time())-120)}
    assert (await client.get("/api/me", headers=stale)).status == 401
    wrong_path = headers(key,token,path="/ws/relay")
    assert (await client.get("/api/me",headers=wrong_path)).status == 401
    claims=jwt.decode(token,options={"verify_signature":False})
    forged=jwt.encode(claims,other,algorithm="EdDSA")
    assert (await client.get("/api/me",headers=headers(key,forged))).status == 401
    claims["exp"]=int(time.time())-10
    expired=jwt.encode(claims,control.signing_key,algorithm="EdDSA")
    assert (await client.get("/api/me",headers=headers(key,expired))).status == 401


async def test_refresh_rotation_reuse_revokes_session(env):
    client, _, _ = env
    key,pending=await enroll(env)
    current=await approve(env,key,pending)
    reused=await client.post("/api/auth/refresh",headers={
        "DPoP":proof(key,"POST","/api/auth/refresh",pending["refresh_token"])},
        json={"refresh_token":pending["refresh_token"]})
    assert reused.status==401
    assert (await client.get("/api/me",headers=headers(key,current["access_token"]))).status==401


async def test_admin_origin_csrf_and_member_permissions(env):
    client,admin,_=env
    payload={"username":"member","password":PASSWORD}
    assert (await client.post("/api/admin/users",headers={"Cookie":admin["Cookie"]},json=payload)).status==403
    bad={**admin,"Origin":"https://attacker.example"}
    assert (await client.post("/api/admin/users",headers=bad,json=payload)).status==403
    created=await client.post("/api/admin/users",headers=admin,json=payload)
    assert created.status==201
    uid=(await created.json())["id"]
    login=await client.post("/api/admin/login",headers={"Origin":ORIGIN},json=payload)
    assert login.status==401
    key,pending=await enroll(env,username="member")
    current=await approve(env,key,pending)
    # Device tokens never act as an administrator cookie.
    client.session.cookie_jar.clear()
    assert (await client.get("/api/admin/overview",headers=headers(key,current["access_token"]))).status==401
    disabled=await client.post(f"/api/admin/users/{uid}/status",headers=admin,json={"enabled":False})
    assert disabled.status==200
    assert (await client.get("/api/me",headers=headers(key,current["access_token"]))).status==401


async def test_gateway_rejects_anonymous_and_closes_revoked_stream(env):
    client,admin,_=env
    with pytest.raises(WSServerHandshakeError) as exc:
        await client.ws_connect("/ws/relay")
    assert exc.value.status==401
    key,pending=await enroll(env)
    token=(await approve(env,key,pending))["access_token"]
    socket=await client.ws_connect("/ws/relay",headers=headers(key,token,path="/ws/relay"))
    await socket.send_bytes(b"encrypted-test-payload")
    assert (await socket.receive(timeout=2)).data==b"encrypted-test-payload"
    await client.post(f"/api/admin/devices/{pending['device_id']}/decision",
                      headers=admin,json={"status":"revoked"})
    msg=await asyncio.wait_for(socket.receive(),5)
    assert msg.type in (WSMsgType.CLOSE,WSMsgType.CLOSED)
    assert socket.close_code==1008
    await socket.close()
    for _ in range(100):
        overview=await client.get("/api/admin/overview",headers=admin)
        records=(await overview.json())["connections"]
        if records and records[0]["ended"]:
            break
        await asyncio.sleep(0.02)
    assert len(records)==1 and records[0]["kind"]=="relay"
    assert records[0]["ended"] is not None
    assert records[0]["bytes_up"]==len(b"encrypted-test-payload")


async def test_setup_consumed_and_passwords_not_exposed(env):
    client,admin,control=env
    assert not control.bootstrap_path.exists()
    res=await client.post("/api/setup",headers={"Origin":ORIGIN},
                          json={"token":"anything","username":"intruder","password":PASSWORD})
    assert res.status==409
    response=await client.get("/api/admin/overview",headers=admin)
    text=await response.text()
    assert PASSWORD not in text and "password_hash" not in text and "refresh_token" not in text
    user=control.store.one("SELECT password_hash FROM users")
    assert user["password_hash"].startswith("$argon2id$")


async def test_fresh_login_replaces_only_the_same_devices_old_session(env):
    client, _, _ = env
    key, pending = await enroll(env)
    old = await approve(env, key, pending)
    other_key, other_pending = await enroll(env)
    other = await approve(env, other_key, other_pending)
    socket = await client.ws_connect("/ws/relay", headers=headers(key, old["access_token"], path="/ws/relay"))
    response = await client.post("/api/auth/login", headers={
        "DPoP": proof(key, "POST", "/api/auth/login")}, json={
        "username": "operator", "password": PASSWORD, "device_name": "Same desktop",
        "platform": "test", "public_key": public(key)})
    assert response.status == 200
    current = await response.json()
    assert current["device_id"] == old["device_id"]
    assert (await client.get("/api/me", headers=headers(key, old["access_token"]))).status == 401
    assert (await client.post("/api/auth/refresh", headers={
        "DPoP": proof(key, "POST", "/api/auth/refresh", old["refresh_token"])},
        json={"refresh_token": old["refresh_token"]})).status == 401
    assert (await client.get("/api/me", headers=headers(key, current["access_token"]))).status == 200
    assert (await client.get("/api/me", headers=headers(other_key, other["access_token"]))).status == 200
    assert (await socket.receive(timeout=5)).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
    await socket.close()
