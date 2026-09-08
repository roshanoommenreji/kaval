# Phase 5 — Mobile app

> **Written from:** theory
> **Lab:** to be written
> **Cost:** $0 additional — Expo local builds, Cognito free at this scale

## Where this sits

Phase 4 put the system somewhere it can run unattended. This phase gives you the only interface
through which anything is ever approved.

It unlocks: the human-in-the-loop is no longer a diagram. Without this, every "approval" is you
running a `curl` command, which is not the claim the project makes.

## What we're doing

- An **Expo** app in TypeScript, six screens
- **Cognito** authentication
- **Push notifications** — incident to phone in under sixty seconds
- Approve and deny, round-tripped to the executor

## Why this way

**Because the approval surface is the product's thesis.** The architecture claim is that a human
gates every consequential action. If approving requires a laptop and a terminal, the claim is
technically true and practically false — you would approve things in batches the next morning,
which is not human-in-the-loop, it is a queue.

A phone changes the economics of the interaction: a decision costs ten seconds instead of ten
minutes, which is what makes fine-grained approval sustainable rather than something you route
around.

The rejected alternative was a web dashboard. Cheaper to build, works everywhere, and gets no push
notifications worth the name, no biometric authentication, and nobody opens it at 2am.

---

## Key concepts

### React Native and what Expo adds

**React Native** renders real native UI components driven by JavaScript. A `<View>` becomes an
Android `ViewGroup`, not a `div` in a webview. That is the difference from a hybrid app: real
native widgets, native scroll physics, native accessibility.

**Expo** is a toolchain and runtime layered on top. It provides:

- A managed build pipeline, so you do not need Android Studio for routine work
- A large library of native modules — notifications, camera, secure storage — already wired up
- Over-the-air updates for JavaScript-only changes

The constraint historically was that arbitrary native code needed "ejecting"; modern Expo handles
this with config plugins and development builds. For this project — network calls, lists, buttons,
notifications — nothing goes near that boundary.

### OAuth 2.0 and OIDC, distinguished

These are constantly conflated and the distinction is simple:

- **OAuth 2.0** is about **authorisation** — granting an application permission to access a resource on your behalf. It answers "may this app do this?"
- **OIDC** (OpenID Connect) is a thin identity layer *on top of* OAuth 2.0. It answers "who is this?" and adds the **ID token**.

You almost always want both: OIDC to establish identity, OAuth to authorise API access.

The flow a mobile app should use is **Authorization Code with PKCE**:

1. App opens a system browser to the identity provider
2. User authenticates there — credentials never touch your app
3. Provider redirects back with a short-lived **authorization code**
4. App exchanges the code, plus a proof it generated at the start, for tokens

**PKCE** (Proof Key for Code Exchange) exists because a mobile app cannot keep a client secret —
anyone can decompile the binary. Instead the app generates a random `code_verifier`, sends its
hash when starting the flow, and sends the original when redeeming the code. An attacker who
intercepts the redirect has a code they cannot exchange.

The older Implicit flow returned tokens directly in the redirect URL. It is deprecated. If you see
it in a tutorial, the tutorial is old.

### JWTs: what they are and what they are not

A **JSON Web Token** is three base64url segments separated by dots: header, payload, signature.

The critical property: **the payload is encoded, not encrypted.** Anyone holding the token can
read every claim in it. Paste one into a decoder and you will see the contents immediately. Never
put anything sensitive in a JWT.

What the signature gives you is **integrity**: the server can verify the token was issued by the
expected authority and has not been altered.

Standard claims worth knowing: `sub` (subject — the user), `iss` (issuer), `aud` (audience),
`exp` (expiry), `iat` (issued at).

Cognito issues three tokens:

| Token | Purpose | Lifetime |
|---|---|---|
| **ID token** | Who the user is; for your app | ~1 hour |
| **Access token** | Authorises API calls; sent to your backend | ~1 hour |
| **Refresh token** | Obtains new tokens without re-authenticating | Days to months |

The short lifetime on access tokens is deliberate: a stolen token expires quickly. The refresh
token is the valuable one and belongs in the platform keystore — `expo-secure-store`, backed by
the Android Keystore — never in `AsyncStorage`, which is plain unencrypted files.

**Validating a JWT means verifying the signature against the issuer's public keys, then checking
`exp`, `iss` and `aud`.** Decoding it and trusting the contents is the classic vulnerability.

### Cognito user pools

A **user pool** is a managed user directory: sign-up, sign-in, MFA, password policy, token
issuance. It is an OIDC provider.

(The other Cognito concept, **identity pools**, exchanges an identity for temporary AWS
credentials so a client can call AWS services directly. Not used here — the mobile app talks only
to the gateway, never to AWS APIs. That is deliberate: it keeps AWS permissions server-side where
they can be reasoned about.)

Free tier covers far more monthly active users than this project will have. For a single-user
system the honest justification is that it is a real, recognisable authentication implementation
rather than a hand-rolled one — and hand-rolling authentication is a well-known way to get it
wrong.

### Push notifications, end to end

This is more moving parts than people expect:

```
gateway ──▶ Expo Push Service ──▶ FCM ──▶ Android device ──▶ app
```

1. On first launch the app requests notification permission
2. It obtains an **Expo push token** identifying this app on this device
3. It registers that token with the gateway, which stores it
4. When a proposal needs approval, the gateway posts to Expo's push API
5. Expo forwards to **FCM** (Firebase Cloud Messaging), which delivers to the device

Properties that shape the design:

- **The token is device-scoped, not user-scoped.** One user, three devices, three tokens. Reinstalling produces a new token; the old one becomes invalid and must be pruned.
- **Delivery is best-effort.** FCM does not guarantee delivery or ordering. A push is a *nudge*, never a transport for data that matters.
- **Payloads are small** — a few kilobytes.
- **Doze mode delays non-urgent notifications.** Android batches background delivery to save battery. A 2am incident alert genuinely needs high priority, which is a decision to make consciously rather than a default to accept.

The design consequence: **the notification carries an incident ID and nothing else.** The app
fetches the real content from the gateway. This is both a size constraint and a security one — the
push path is not something you should trust with incident detail.

### Designing for the 2am decision

The screen someone reads half-asleep is a genuine design problem, and the constraint is different
from a dashboard's.

What the approval screen must answer, in order:

1. What broke?
2. What does the agent think caused it?
3. What exactly will happen if I approve?
4. What happens if I do nothing?

Point four is the one usually missing, and it is what makes an approval a decision rather than a
reflex. "Deny" must be as easy as "approve," and the consequence of denying must be stated.

Approve should require deliberate action — a confirmation, or biometric authentication for
higher-risk actions — because the cost of an accidental approval is asymmetric with the cost of an
accidental dismissal.

---

## Common mistakes

| Mistake | What it costs |
|---|---|
| Storing refresh tokens in `AsyncStorage` | Plain unencrypted files; any device compromise takes the account |
| Decoding a JWT and trusting it without verifying the signature | Trivial forgery |
| Putting sensitive data in JWT claims | Base64 is not encryption; the client reads everything |
| Using the Implicit flow | Deprecated; tokens exposed in the redirect URL |
| Omitting PKCE in a mobile app | Interception of the authorization code becomes exploitable |
| Treating a push token as a user identifier | Breaks on reinstall and on a second device |
| Relying on push delivery for correctness | Best-effort; some notifications simply do not arrive |
| Putting incident detail in the push payload | Size limits, and an untrusted delivery path |
| Making "approve" the easy path and "deny" awkward | Approval becomes a reflex; the human gate stops being a gate |

## Glossary

| Term | Meaning |
|---|---|
| **React Native** | Renders real native components driven by JavaScript |
| **Expo** | Toolchain and runtime over React Native |
| **OTA update** | Shipping JavaScript changes without an app-store release |
| **OAuth 2.0** | Authorisation framework — may this app do this? |
| **OIDC** | Identity layer over OAuth 2.0 — who is this? |
| **Authorization Code flow** | Redirect returns a code, exchanged server-side for tokens |
| **PKCE** | Proof Key for Code Exchange; secures the code flow for public clients |
| **JWT** | Signed token of three base64url parts; payload is readable by anyone |
| **Claim** | A field in a JWT payload |
| **ID token** | Asserts who the user is |
| **Access token** | Authorises API calls; short-lived |
| **Refresh token** | Obtains new tokens without re-authentication; long-lived and valuable |
| **User pool** | Cognito's managed user directory and OIDC provider |
| **Identity pool** | Exchanges identity for temporary AWS credentials; not used here |
| **JWKS** | The issuer's published public keys, used to verify signatures |
| **FCM** | Firebase Cloud Messaging — Android's push transport |
| **Push token** | Identifies an app installation on a device; device-scoped |
| **Doze mode** | Android battery optimisation that delays background delivery |
| **`expo-secure-store`** | Keychain/Keystore-backed encrypted storage |

## Check yourself

1. What does OIDC add to OAuth 2.0, in one sentence?
2. Why can't a mobile app hold a client secret, and what replaces it?
3. Someone stores a user's role in a JWT claim and reads it client-side to hide admin buttons. Two things are wrong. What?
4. Why do access tokens expire in an hour when refresh tokens last for weeks?
5. Your user reinstalls the app and stops getting notifications. Why, and what should the gateway do about stale tokens?
6. Why does the push payload carry only an incident ID?
7. Name the four questions the approval screen must answer, and say which one is usually missing.

## In an interview

**"Why build a mobile app rather than a web dashboard?"**

> "Because the architecture's central claim is that a human approves every consequential action,
> and that claim is only true if approving is cheap. On a laptop I'd have batched approvals the
> next morning, which isn't human-in-the-loop, it's a queue with extra steps. On a phone a
> decision costs ten seconds, so fine-grained approval stays sustainable instead of being routed
> around. Practically it also gives me push notifications and biometric confirmation, neither of
> which a web dashboard has. The design constraint I found most interesting was the approval
> screen itself — it has to be readable at 2am, and it has to state what happens if you decline,
> not just what happens if you approve. Otherwise approval becomes a reflex and the gate stops
> being a gate."

The last two sentences are what separates this from a feature list.

## Further reading

- RFC 6749 (OAuth 2.0) and RFC 7636 (PKCE) — skim the abstracts, read the flow diagrams
- OAuth 2.0 for Native Apps (RFC 8252) — why the system browser, not a webview
- AWS Cognito Developer Guide — user pools, token types and lifetimes
- Expo documentation — *Push Notifications* and *SecureStore*
- Android developer documentation — Doze and App Standby
