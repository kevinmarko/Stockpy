# Security Policy

## Supported Versions

The InvestYo Quant Platform ("Stockpy") receives active security updates on the main branch.

| Version | Supported          |
| ------- | ------------------ |
| `main`  | :white_check_mark: |
| Development branches | :white_check_mark: |

## Reporting a Vulnerability

We take the security of the InvestYo Quant Platform seriously. If you believe you have found a security vulnerability in any module (including execution quarantine, signal generation, API endpoints, MCP server, or webapp), please report it responsibly.

### How to Report

- **Email**: Contact the repository maintainer directly at `kevin.marko@gmail.com`.
- **Do NOT open a public issue** on GitHub for security vulnerabilities or credential exposure.

### What to Include

Please provide:
1. A clear description of the vulnerability and affected components.
2. Steps to reproduce or proof-of-concept script.
3. Potential impact (e.g., unauthorized order placement, credential leak, API bypass).

### Security Architecture Highlights

- **Advisory-First Quarantine**: Live broker order placement is quarantined behind explicit environment gates (`ADVISORY_ONLY=True`, `ROBINHOOD_EXECUTION_MODE=off`, `LIVE_TRADE_EXECUTION_ENABLED=false`).
- **Secret Protection**: `.env` files are ignored by git and guarded against programmatic writes.
- **Dependency Audits**: Continuous Integration runs automated `pip-audit` and static AST security scanning on every push.
