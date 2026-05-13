# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 1.x (latest) | ✅ Active |
| < 1.0 | ❌ Pre-release — upgrade to 1.x |

---

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

This project handles database access from AI agents and sits in an enterprise security boundary. Responsible disclosure matters.

### How to report

Email: **security@open-gw.io** (or open a [GitHub Security Advisory](https://github.com/open-gw/gateway-db-mcp/security/advisories/new) for private disclosure)

Include:
- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact
- Any suggested fix (optional but appreciated)

### What to expect

| Step | Timeline |
|---|---|
| Acknowledgement | Within 48 hours |
| Initial assessment | Within 7 days |
| Fix or mitigation | Within 30 days for critical issues |
| Public disclosure | After fix is released and users have had time to update |

We will credit you in the release notes unless you prefer to remain anonymous.

---

## Security model

Understanding the security architecture helps scope valid reports:

**In scope:**
- SQL injection bypasses in `QueryValidator` that are not already documented in `SECURITY.md` or Javadoc
- Credential exposure through log statements, error messages, or pool key generation
- Path traversal or injection through table/column identifier validation
- Connection pool isolation failures in multi-tenant Apigee environments
- Docker image vulnerabilities in base image or bundled dependencies

**Out of scope (known limitations, documented):**
- MySQL conditional comments (`/*!50000 SELECT */`) bypassing the regex validator — documented; mitigated by Layer 1 (read-only DB user)
- Unicode normalization bypass of keyword detection — documented
- Database-specific syntax not in the denylist — documented
- Full SQL injection prevention without a read-only database user — the validator is explicitly documented as defence-in-depth requiring Layer 1

**Not our responsibility:**
- Security of the Apigee, Kong, or Azure APIM gateway layer
- Security of the database server itself
- Operator failure to provision a read-only database credential (this is documented as mandatory)
