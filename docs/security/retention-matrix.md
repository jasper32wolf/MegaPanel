# Data Retention Matrix (TZ 12.5)

| Data type | Retention | Legal basis | Deletion |
|-----------|-----------|-------------|----------|
| Leads PII | 12 months | Contract / consent | DSAR + cron |
| Auth logs | 6 months | Legitimate interest | Auto purge |
| Analytics (anon) | 24 months | Consent | Auto purge |
| Audit hash-chain | 5 years | Compliance | Archive cold storage |
| Build artifacts | 90 days (keep last 3) | Ops | Disk GC |
