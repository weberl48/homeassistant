# Product

<!-- impeccable:product-schema 1 -->

<!-- Scope: this PRODUCT.md governs the Bootlegger app (bootlegger/) only, not the
     other apps in this household monorepo. Facts below derive from the owner's
     BOOTLEGGER design & build plan (2026-08) and the explicit instruction to build
     it; items marked [inferred] were not separately confirmed. -->

## Platform

web

## Stack

Python 3.12 + FastAPI + SQLite (WAL) on a Raspberry Pi 5 behind Tailscale; the
primary client is a sideloaded Expo/React Native Android app, with a
server-rendered static web page as the always-available fallback surface (pinned
by the brief). The web surface is plain static HTML/CSS/JS served by FastAPI —
no build step, so draft day never depends on a toolchain. [Stack pinned by the
brief; the no-build-step choice for the web page is inferred from the
reliability requirement.]

## Users

One person: the league owner, drafting and managing a single 12-team full-PPR
Sleeper fantasy football league. Two scenes: (1) draft night, laptop or phone in
a dim living room, picks arriving every few seconds, ~60 seconds to decide;
(2) Sunday mornings and weeknights, phone in hand, deciding whether to approve a
recommended lineup swap before kickoff. Glanceable, time-pressured, single-user.

## Product Purpose

A personal Sleeper autopilot that matches FantasyPros' analysis (draft board
with tiers/VBD/survival odds, start-sit, waivers, trades, injury alerts) and
exceeds it with an actuation layer FantasyPros doesn't have: approval-gated,
self-verifying lineup swaps executed against the owner's own account. Success =
the draft board keeps pace with a live draft (picks crossed off within 3s), and
lineup recommendations arrive as push notifications that can be approved in one
tap and verified against the public API.

## Positioning

"The legitimate storefront with a discreet back room": rankings and rationale up
front, robot hands in the back. The one claim neighbors can't copy — it is
calibrated to this league's literal scoring settings and this league's actual
FAAB bid history, and it can act (lineup swaps only), not just advise.

## Operating Context

- Sleeper public API is read-only; all data polls in from it (draft picks at 2s
  cadence during the draft). Writes happen only through the separate "hands"
  worker, gated by explicit approval.
- Runs next to Home Assistant on the household Pi; reachable only over
  Tailscale. Push arrives via Expo/FCM (outbound-only from the Pi).
- Demo mode simulates a full snake draft and a suboptimal Sunday lineup so every
  surface can be rehearsed locally with no league and no network.

## Capabilities and Constraints

- Blast-radius containment is structural: the only automated write in the
  codebase is a reversible lineup swap. Waivers/FAAB/trades have no actuation
  code path.
- Reliability during the Sunday window is the top non-functional requirement:
  every failure degrades to a notification, never to silence. The web surface
  must stay legible and functional with no JS framework, no CDN, and no
  external font/network dependency at render time (assets self-hosted).
- State machine vocabulary (product terminology): proposed → notified →
  approved | snoozed | ignored → executed → verified | failed.
- Actuation default is approve_required; auto_execute is opt-in after three
  clean weeks. [Owner's recommended default, adopted per "build this".]

## Brand Commitments

The household aesthetic is pinned: Speakeasy Cinema. Bootlegger's identity
belongs to that world — Prohibition-era back-room material culture (walnut and
brass, ledger paper, ticker tape, banker's-lamp green, oxblood), rendered as a
working tool, not a costume. The name "BOOTLEGGER" is established. Voice: dry,
confident, a little clandestine ("the back room", "the ledger"); never cutesy
where money or kickoffs are involved.

## Evidence on Hand

- The owner's full design document (requirements, architecture, data model,
  pinned math, phases) — docs/DESIGN-PLAN.md in this app.
- The household repo's existing Speakeasy HA configuration confirms the
  aesthetic commitment.
- No real league data ships with the repo; demo data is synthetic and labeled.
  Player names in the demo fixture are real NFL players [2025-season knowledge;
  refreshed from the live Sleeper API in real mode].

## Product Principles

1. Never silently fail — every degraded state is visible and pushes outward.
2. Advise everywhere, act in exactly one place, verify through the public API.
3. The board must be readable at a glance under time pressure; density with
   hierarchy beats completeness.
4. Own the whole stack locally: no external service in the critical path except
   Sleeper itself.
5. Earn trust in stages: notify → approve → (later) auto, with an audit trail.

## Accessibility & Inclusion

Single known user, but the Sunday surface is used one-handed on a phone under
DND stress: minimum 44px touch targets on approval actions, status never
conveyed by color alone (icons/labels accompany), respects
prefers-reduced-motion. [inferred from usage scene]
