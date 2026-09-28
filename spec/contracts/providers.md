# Provider connection contract

Every provider exposes a capability record and a safe connection snapshot.

```text
ProviderConnectionSnapshot:
  provider_id: string
  display_name: string
  auth_modes: [api, official_cli, oauth, device]
  auth_state: CONNECTED | NOT_CONFIGURED | AUTH_REQUIRED | EXPIRED | RATE_LIMITED |
              DEGRADED | UNKNOWN | DISABLED | UNSUPPORTED
  billing_mode: api | subscription | disabled | unknown
  credential_reference: string | null
  usage_state: AVAILABLE | UNKNOWN | UNAVAILABLE
  context_window: integer | null
  session_limit: decimal | null
  four_hour_limit: decimal | null
  weekly_limit: decimal | null
  limit_source: CONFIGURED | OFFICIAL | UNKNOWN
  subscription:
    supported: boolean
    cli_available: boolean
    auth_state: CONNECTED | AUTH_REQUIRED | DEGRADED | UNKNOWN | UNSUPPORTED
    plan: string | null
    usage_state: AVAILABLE | UNKNOWN | UNAVAILABLE
    context_remaining_percent: decimal | null
    session_remaining: decimal | null
    four_hour_remaining: decimal | null
    weekly_remaining: decimal | null
    limit_source: OFFICIAL_CLI | OFFICIAL_API | UNKNOWN
    last_checked_at: timestamp | null
    detail: string
  last_checked_at: timestamp | null
  last_error_code: string | null
  recovery_action: string
```

Secrets are never serialized into this contract. API tests must perform a provider
healthcheck, not merely check that a string exists.
