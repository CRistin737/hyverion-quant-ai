# Ingestion contract

Every connector run follows:

```text
fetch -> validate -> sanitize -> normalize -> dedupe -> freshness -> persist -> trigger
```

`ExternalItem` stores only a bounded sanitized extract, content hash, canonical URL,
source id, asset mapping, event/received/processed timestamps and provenance. External
content is wrapped in `UNTRUSTED_EXTERNAL_CONTENT` before any agent sees it.

Normal market ticks update deterministic storage only. Agents wake on configured events,
scheduled reviews or position-protection triggers.
