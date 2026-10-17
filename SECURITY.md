# Security

## What panda-print is, and is not

panda-print has **no login by design**. It is meant for a home or office network, behind a reverse proxy that does the
signing in (single sign-on, basic auth or similar). Do not expose it to the internet directly.

What it does to protect itself:

- Every `POST`, `PUT`, `PATCH` and `DELETE` under `/api/` needs a signed CSRF token and a matching `Origin`.
- A strict Content-Security-Policy (no inline script or style, no external hosts for scripts), and uploaded files are served sandboxed with `nosniff`.
- Addresses typed in the Printers page are checked, and link-local and multicast addresses (where cloud metadata services live) are refused.
- Printing is rate-limited per printer, counted from the database.
- Secrets are not committed. Nothing in the repository is a credential, and the SNMP community is configuration.

Anyone who can reach the dashboard can add a printer, upload pages and print. Treat access to it as an admin-level permission.

## Reporting a problem

Please report a vulnerability privately: on the repository's **Security** tab choose **Report a vulnerability** (a
GitHub private security advisory). Please do not open a public issue for it. Include what you saw, how to reproduce it
and the version (`/api/health` shows it). I will reply as soon as I can, and credit you if you like once it is fixed.

Only the latest release is supported.
