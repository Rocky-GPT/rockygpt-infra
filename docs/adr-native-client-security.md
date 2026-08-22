# ADR: future native-client authentication

Status: accepted for future implementation; no endpoint is created by this work.

Native iOS and Android applications must not embed OpenAI keys, database
credentials, admin tokens, abuse-signing secrets, or the staging service token.
An extracted application bundle is a public artifact regardless of store review.

When native clients are introduced, they will call a RockyGPT-controlled server
that issues short-lived, narrowly scoped client tokens after verifying Apple App
Attest or Google Play Integrity evidence. The server will enforce expiry,
audience, environment, replay resistance, rate limits, and revocation. The
existing brain and data APIs remain the internal service boundaries; this ADR
does not authorize a gateway, token endpoint, iOS app, or Android app today.
