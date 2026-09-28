# Source contract

```text
SourceDefinition:
  source_id: string
  category: market | news | social | derivatives
  region: string
  transport: api | rss | html
  base_url: string
  allowed_hosts: [string]
  allowed_paths: [string]
  terms_url: string | null
  robots_url: string | null
  html_allowed: boolean
  poll_interval_seconds: integer
  rate_limit_per_minute: integer
  parser_version: string
  compliance_status: PENDING_REVIEW | APPROVED | REJECTED
  enabled: boolean
```

HTML collection is allowed only for an `APPROVED` definition with explicit
`html_allowed=true`. A 403, 429, CAPTCHA, robots denial or unexpected redirect pauses
the source and records a connector failure; it never triggers bypass behavior.
