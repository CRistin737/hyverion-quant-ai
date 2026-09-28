# Collection safety policy

Hyverion never guarantees zero blocking; it minimizes risk by respecting source rules.

- APIs and RSS are preferred.
- HTML is allowed only after recorded ToS/robots review.
- Hosts, paths, payload size, cadence and rate limits are allowlisted.
- Cache validators, bounded retries and exponential backoff are mandatory.
- No proxy rotation, fingerprint spoofing, CAPTCHA solving or robots bypass.
- Authenticated/private pages are out of scope.
- A source without permission becomes `DISABLED_UNSUPPORTED`.
- External text is data only and cannot alter agent instructions.
