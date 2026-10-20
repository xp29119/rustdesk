# Private control platform

The platform owns accounts, device approval, revocable sessions and an authenticated
WebSocket entrance for RustDesk. The administration UI is Chinese and requires a
separate administrator cookie. There is no public account registration.

## Status and boundaries

- Ten local tests pass (eight API/auth tests and two certificate-proxy tests); browser login, device listing and approval have been
  exercised using temporary test data.
- The gateway has been tested against a binary WebSocket test backend. Native
  RustDesk client integration and end-to-end remote desktop tests remain pending.
- The IP entrance now uses a dedicated application CA. Only the custom clients trust it; no system root certificate is installed.
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

Activation requires the reviewed internal-CA configuration. It:

1. Validates Caddy as its restricted service user, with system trust installation disabled.
2. Blocks external TCP 21115–21119 and UDP 21116, including the old direct path.
3. Allows TCP 80/443 for the HTTPS entrance/redirect, preserving existing SSH and AWG rules.
4. Restricts the RustDesk systemd services to loopback networking.
5. Starts Caddy and the RustDesk backends.

Caddy advertises HTTP/1.1 and HTTP/2 only so it does not bind UDP 443, which is
already used by AWG. The trusted proxy overwrites `X-Real-IP`; the application
trusts that header only from loopback. Neither component logs bearer credentials.

## Dedicated certificate trust

The user approved a dedicated internal CA after public ACME validation failed.
The Mac tunnel fixed outbound ACME access, but Let's Encrypt's inbound HTTP-01
and TLS-ALPN-01 connections to the server both timed out. No public IP certificate
was issued. This network failure is not bypassed by disabling TLS validation.

Caddy now uses `tls internal`, `skip_install_trust`, and `default_sni 8.138.230.183`.
Its restricted data directory `/var/lib/rdcontrol-caddy` holds the CA and server
private keys. Only the public CA certificate is copied to `certs/rdcontrol-root.pem`
in the client repository. Its DER SHA-256 fingerprint is
`ceaadd7b8ccdb67654b3f64102deb248590391e3852ed2c1cb6f0536a5c085bc`.

Managed HTTPS and WSS trust only that root, validating the chain, expiry and fixed
IP. They do not install system trust, use permissive platform fallback, or downgrade
to HTTP. Normal browsers are not expected to trust the IP certificate; administrators
use the existing portal's publicly trusted HTTPS address.

`enable_internal_ca.py` transitions the existing installation using a narrow Caddy
service override. The deployed runtime config is `/etc/rdcontrol/Caddyfile.internal`;
the override is `rdcontrol-caddy.service.d/internal-ca.conf`. Preserve these deployment
choices when updating service files. The CA must not be regenerated during upgrades.
A CA replacement requires rebuilding and redistributing clients; the workflow watches
`certs/**` for this reason. Keep encrypted backups of the server's private data.

Live verification passed for the trusted IP certificate, anonymous API/WSS rejection,
public administration denial, native-port isolation, wrong-IP rejection, and rejection
without the dedicated root. The Rust HTTPS and WSS deployment probes also passed.
Caddy maintains internal certificates automatically. Restart retained the CA; a full
natural renewal interval has not yet been observed.

The approved Mac link continues to carry only the private administration page via
Mac loopback `18490` to server loopback `8088`. Its account cannot use shell, commands,
SFTP or arbitrary forwarding. Denial and automatic SSH reconnection were verified.
The earlier certificate proxy/reverse forwarding is no longer required for renewal;
retire only those task-created resources after final validation, preserving management
forwarding and all existing Seqdao/DDNS/Surge/original login services.

## Existing Seqdao management entrance

The user chose to preserve RustDesk's independent login service while presenting
its management page through the existing Seqdao portal. The backend 0.1.2
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

The portal-only three-file patch is reviewed and being deployed separately.
Do not replace the existing Seqdao deployment directory.
The public Caddy entrance allows only the six client authentication/WSS
paths; administrative pages and APIs are reachable through the private portal
channel. Caddy's separate administrative configuration API is disabled.

## Audit semantics

Account events come from the authentication server. Connection rows are
WebSocket transport observations: opening/closing time and byte counts.
They do not claim a successful remote-control login or a complete desktop session.
An interrupted process is recorded with an unknown ending, not an invented time.

## Remaining work

The loopback service 0.1.2 is healthy at `127.0.0.1:8088`. RustDesk hbbs/hbbr are active
with loopback-only systemd networking and external native-port firewall drops.
Dedicated-CA HTTPS is active, but no production administrator has been initialized.

Complete the real administrator portal setup, finish dedicated-CA native builds,
then test approved login, service IPC, protected storage, remote desktop connection,
revocation and reboot behavior on all three platforms before distributing installers.
Earlier Android/macOS ARM64 build artifacts predate the CA change and are not final.
