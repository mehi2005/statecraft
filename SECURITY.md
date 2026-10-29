# Security policy and reporting status

Statecraft is a local evaluation harness with synthetic data and mock enterprise
APIs. The mock tools intentionally allow actions that violate the simulated
company policy so the verifier can detect unsafe agent behavior. Those actions
alone are not evidence of a security vulnerability in Statecraft.

Run the HTTP server on localhost and use synthetic data. The current server has
one shared environment and no authentication; it is not suitable for public or
multi-tenant deployment. Do not connect real customer records, vendor credentials,
or payment systems to these examples.

## Reporting a suspected vulnerability

No verified private vulnerability-reporting route is currently documented for
this repository. Do not post exploit details, credentials, personal data, or
other sensitive material in public issues or pull requests. If you need to
request a reporting channel, limit any public coordination to that request and
omit the vulnerability details.

Maintainer action needed: establish and verify a private reporting channel, then
publish its instructions here. This document does not assert that GitHub private
vulnerability reporting is enabled or that a security contact address exists.
There is currently no published security-support version policy or response-time
commitment.

For ordinary reproducible bugs that do not require sensitive disclosure, use the
bug-report template and include a minimal example with synthetic data.
