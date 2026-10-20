# Private control platform

The platform owns accounts, device approval, revocable sessions and an authenticated
WebSocket entrance for RustDesk. The administration UI is Chinese and requires a
separate administrator cookie. There is no public account registration.

## Status and boundaries

- Ten local tests pass (eight API/auth tests and two certificate-proxy tests); browser login, device listing and approval have been
  exercised using temporary test data.
- The gateway has been tested against a binary WebSocket test backend. Native
  RustDesk client integration and end-to-end remote desktop tests remain pending.
- Public HTTPS deployment requires the certificate-only egress described below.
- The first version uses WSS and relay transport. It does not promise P2P
  performance or label an open transport as a successful remote desktop session.
- An approved endpoint retains RustDesk's own remote-control password/consent
  requirements. This platform grants use of the server, not unattended access to
  every remote desktop.

## Run and test

Use Python 3.12 and the pinned packages in `requirements.lock`.

```sh
python -m rdcontrol --data-dir /path/to/private/data --public-url https://8.138.230.183
python -m pytest -q
```

The service binds only to `127.0.0.1:8088`. For a local preview, an HTTP public
URL is allowed only on `127.0.0.1` or `localhost`. Production URLs require HTTPS.
The process generates an Ed25519 issuer key and a one-use bootstrap code in the
private data directory. The code is never printed or served by an API.

## Native client protocol

API base: `https://8.138.230.183`.
All device requests use Ed25519 DPoP JWTs:

- Protected header: `typ=dpop+jwt`, `alg=EdDSA`,
  `jwk={kty:OKP, crv:Ed25519, x:<base64url public key without padding>}`.
- Payload: `jti` (fresh UUID), `iat` (current Unix seconds), `htm` (HTTP method)
  and `htu` (exact HTTPS URL without query).
- For access or refresh credentials, `ath` is the base64url SHA-256 hash of that
  credential. The proof must use the enrolled device's private key.
- For the WSS handshake, `htu` uses the HTTPS handshake URL, such as
  `https://8.138.230.183/ws/id`, and `htm=GET`.
- Proofs have a 30-second freshness window and a persistent replay check.

Endpoints:

| Method and path | Body / authentication | Result |
| --- | --- | --- |
| POST `/api/auth/login` | username, password, device_name, platform, public_key; DPoP | Pending device: 202 with refresh credential, no access token. Approved device: 200. |
| POST `/api/auth/refresh` | refresh_token; DPoP bound to refresh token | Rotated refresh token; access token only for approved devices. |
| GET `/api/me` | Bearer access token + DPoP | Current approved device. |
| POST `/api/auth/logout` | Bearer access token + DPoP, or refresh_token body + bound DPoP | Session revoked, including devices still awaiting approval. |
| GET `/ws/id` | Bearer access token + DPoP | Protected signaling WebSocket. |
| GET `/ws/relay` | Bearer access token + DPoP | Protected relay WebSocket. |

Access tokens last five minutes. Refresh credentials expire after 30 days of
inactivity and rotate every use. Reuse of an old refresh token revokes its session.
The client must replace the stored refresh token atomically and serialize refresh
operations. A lost refresh response may require login again; no unsafe retry
grace period is provided.

Private device keys and refresh credentials belong in OS-protected storage.
They must not enter URLs, logs, plain configuration files or GitHub artifacts.
The service rechecks authorization while channels are active, at a two-second
interval. The final client application must also respect logout and expiry.

## Deployment

`deploy/install_platform.py` is turned into a checksum-bound offline installer
when the deployment bundle is prepared. Without `--activate-gateway` it installs
only the loopback control service. It does not open HTTPS or start RustDesk.

Activation requires a working certificate-only SSH tunnel. It:

1. Validates Caddy and the tunnel.
2. Blocks external TCP 21115–21119 and UDP 21116, including the old direct path.
3. Allows TCP 80/443 for ACME and HTTPS, preserving the existing SSH and AWG rules.
4. Restricts the RustDesk systemd services to loopback networking.
5. Starts Caddy and the RustDesk backends.

Caddy advertises HTTP/1.1 and HTTP/2 only so it does not bind UDP 443, which is
already used by AWG. The trusted proxy overwrites `X-Real-IP`; the application
trusts that header only from loopback. Neither component logs bearer credentials.

## Certificate transport

The current candidate uses the user's domestic Mac, without binding any domain
to ali. Read-only checks on that Mac confirmed access to ali TCP 22 and both ACME
directories through its existing Surge HTTP proxy at `127.0.0.1:6152`.
No new Mac/ali authorization has yet been installed.

Prepared scripts:

- `mac_cert_proxy.py`: loopback `18488`, only HTTP CONNECT to the two exact ACME
  hostnames on TCP 443; always through Surge, no direct fallback or TLS interception.
- `install_mac_link.py`: separate ordinary-user LaunchAgents and a dedicated
  SSH identity. Preparation and activation are explicit separate steps.
- `authorize_mac_link.py`: dry-run by default; adds the key-only account
  `rd-mac-webcert` only with `--apply`. No sudo/shell/commands/SFTP. `PermitOpen`
  limits local forwarding to ali `127.0.0.1:8088`; `PermitListen` limits reverse
  forwarding to ali `127.0.0.1:18489`. The key itself carries the same restrictions.

The proposed combined link carries the management page via Mac loopback `18490`
and certificate egress via ali loopback `18489`. It carries no remote-desktop
data. Caddy's `HTTPS_PROXY` is `http://127.0.0.1:18489`. Its certificate private key
stays on ali. A Mac reboot still requires the ordinary user session and Surge;
pre-login availability has not been verified. Actual issuance, reconnection and
renewal acceptance remain required, including the CA's inbound challenge.

### Earlier overseas attempt

The directly verified exit is `194.156.163.226`. Its Ed25519 host fingerprint is:
`SHA256:E8ZPynQ4ZbYLvlY9Jz/+LHKHzj5WzbtmIQ9seRDnbRs`.

`prepare_certificate_client.py` generates a dedicated private key on ali gz.
`authorize_certificate_exit.py` uses only the matching public key on the exit.
The proposed exit account is `rd-cert`, restricted to:

- Source IP `8.138.230.183`.
- Forwarding to the production and staging Let's Encrypt ACME hosts on TCP 443.
- No shell, SFTP, agent/X11 forwarding, reverse forwarding or public proxy port.

The user approved this account and it is installed. A dedicated SSH listener on
TCP 443 accepts only this account, and its firewall allows only `8.138.230.183`.
Original SSH 22 and the existing service on 18786 were preserved. Both directions
between these two servers timed out; the source's cloud firewall was inspected
and no relevant block was found. The ali-side tunnel has therefore not been
activated. Do not treat this route as a functioning renewal path. The earlier
deployment used `HTTPS_PROXY=socks5h://127.0.0.1:1080`; the new draft replaces it
with the Mac route only after approval. Backend requests use loopback directly.
The HTTPS certificate private key stays on ali gz.

## Existing Seqdao management entrance

The user chose to preserve RustDesk's independent login service while presenting
its management page through the existing Seqdao portal. The backend draft 0.1.2
accepts `--admin-origin`, `--admin-cookie-path`, and `--admin-cookie-name` for this purpose. They do not
change the native client's public URL, JWT issuer or DPoP destination.

For `/apps/rustdesk/`, a private gateway strips that prefix before proxying.
HTML resources and API calls now resolve relative to the page's own prefix.
The external prefix must end with `/`; redirect the slashless URL in the portal.
The administrator cookie must use the distinct name `rdcontrol_admin`, be scoped
to `/apps/rustdesk/`, and use the exact portal HTTPS origin. A real browser test
with an existing unrelated root-path cookie verified login, reload and approval.
Keep the page outside an iframe; its frame protections
remain enabled. The portal gateway must enforce existing portal authorization,
proxy only the management allowlist and `rdcontrol_admin` cookie, and strip all Seqdao credentials/identity
headers. RustDesk still requires its own administrator cookie and CSRF token.

The remote helper is preparing the portal-only patch separately. It has not
been installed. Do not replace the existing Seqdao deployment directory.
The draft public Caddy entrance allows only the six client authentication/WSS
paths; administrative pages and APIs are reachable through the private portal
channel. Caddy's separate administrative configuration API is disabled.

## Audit semantics

Account events come from the authentication server. Connection rows are
WebSocket transport observations: opening/closing time and byte counts.
They do not claim a successful remote-control login or a complete desktop session.
An interrupted process is recorded with an unknown ending, not an invented time.

## Remaining work

The loopback service 0.1.1 is installed and healthy at `127.0.0.1:8088`; no production
administrator has been initialized. The deployed version includes
refresh-authenticated logout for pending devices.
Public HTTPS is not active; RustDesk hbbs/hbbr remain stopped and disabled.

Resolve a reliable certificate issuance and renewal route; update the loopback
service; validate HTTPS and native-port isolation; compile and test native login,
service IPC and protected credential storage on all three platforms; run actual
RustDesk connection/revocation/reboot tests; then deliver internal installers.
The added Rust client module has local protocol tests, not completed installer QA.
