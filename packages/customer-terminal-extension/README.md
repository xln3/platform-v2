# GEO Customer Terminal extension

This Manifest V3 extension is the V2 customer-device execution boundary. It generates an Ed25519 private key with
`extractable=false`, stores the `CryptoKey` in IndexedDB, pins the task-signing public-key fingerprint from a
locally decoded one-time GEO pairing QR, verifies every signed task, and opens only the declared HTTPS hostname.
It uses the browser's native QR detector when available and an Apache-2.0 jsQR 1.4.0 bundle as an entirely local
fallback; neither path loads remote code or sends image pixels off the device.

Terminal bind/result requests omit credentials, redirects, referrers and HTTP caching, explicitly request JSON
and accept only bounded `application/json` response bodies. The exact generated six-field task envelope and
five-field result receipt are validated at the background boundary. Both outer and signed task expiries must be
strict, possible, timezone-qualified ISO timestamps for the same instant and no more than five minutes ahead;
ambiguous browser-parsed dates never enter session storage.

The extension never requests cookie, proxy, scripting, web-request or page-content permissions. It does not
accept or upload target-platform OTP/QR values, face images/video, passkey private keys, session state or
screenshots. The pairing QR image is decoded locally, never uploaded, and the one-time token is kept only in
popup memory until it is consumed by the GEO bind endpoint. There is no text field or URL path for pasting the
token. The customer completes the native challenge on the target platform. The extension stores only an
allow-listed task projection and submits a signed `challenge_completed`, `failed` or customer-selected `rejected`
result; GEO must still obtain a separate platform callback or identity probe before resuming the account after a
claimed completion. Failure and rejection do not change the existing authorization or session. A response is
accepted only when its exact five-field receipt matches the signed task and submitted result.

Opening the declared platform closes a normal browser-action popup. The verified task therefore survives only in
`chrome.storage.session`: reopening the popup validates the exact stored shape and expiry again, renders only the
action/domain/challenge projection and disables new pairing inputs. Invalid, expired or extended stored objects
are removed instead of rendered; a safely recognized expiry is announced without exposing task fields. The
restored task exposes completion, native-verification failure and rejection, disables all three actions while one
signed result is in flight, and prevents duplicate submission. The initial and restored states have explicit
keyboard focus indicators, status announcements, forced-color support and real-browser WCAG-AA coverage.

Load `dist/` as an unpacked extension after `pnpm --filter @geo/customer-terminal-extension build`. A production
release must be signed and distributed through the organization-controlled immutable CRX endpoint, Chrome/Edge
store or managed policy. Source or artifact readiness is not evidence of an authorized customer installation or
native challenge canary.
