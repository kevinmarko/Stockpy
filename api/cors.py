"""
api/cors.py
===========
Shared CORS origin-matching helper for every standalone service in
``api/*.py``. Single source of truth for widening the CORS allowlist to
cover LAN and Tailscale access, ADDITIVE to (never instead of) the
operator's explicit ``settings.CORS_ALLOWED_ORIGINS`` list.

Why a regex instead of another entry in CORS_ALLOWED_ORIGINS
--------------------------------------------------------------
A LAN IP is DHCP-assigned and can change on lease renewal; a Tailscale
address is per-device and per-tailnet. Hard-coding one machine's current
address into ``.env`` would silently stop working the next time the address
changes -- no error, just requests quietly failing CORS again. This module
instead matches on the RFC 1918 private-network ranges (192.168.0.0/16,
10.0.0.0/8, 172.16.0.0/12) and Tailscale's carrier-grade-NAT range
(100.64.0.0/10), scoped to the Pilots PWA dev server's port (5173, per
``webapp/vite.config.ts``'s ``server: { host: true, port: 5173 }`` --
``host: true`` already binds it to 0.0.0.0, so any LAN/Tailscale device can
reach it; the API's CORS policy was the remaining blocker) -- so any device
on the operator's LAN or tailnet is covered automatically, without ever
widening access to the public internet.

Passed as Starlette's ``allow_origin_regex``, which is OR'd against the
exact-match ``allow_origins`` list (see
``starlette.middleware.cors.CORSMiddleware.is_allowed_origin`` --
``re.fullmatch``, so no further anchoring is needed here). The operator's
explicit ``CORS_ALLOWED_ORIGINS`` still governs any origin outside these
ranges (e.g. a real deployed public domain).

This only decides whether the BROWSER lets the response through -- every
``api/*.py`` service still requires its own bearer token (``api/auth.py``)
for anything beyond an open read; this grants no authorization on its own.
"""

from __future__ import annotations

LAN_TAILSCALE_ORIGIN_REGEX = (
    r"http://("
    r"192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
    r"):5173"
)
