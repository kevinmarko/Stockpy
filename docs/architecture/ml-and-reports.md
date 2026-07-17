# Architecture: ML Pipeline, Report Templates & AI Auditor

> Part of the split-out `CLAUDE.md` System Architecture reference. See [../../CLAUDE.md](../../CLAUDE.md) for the index.

- **reports/cpcv_report.html.j2** — Plotly/Jinja template for CPCV and overfitting validation reports.
- **reports/validation_report_template.html.j2** — Jinja2 template for rendering validation reports.
- **ai_verification_prompts.py** ("Gravity AI Auditor") — 6-step static-analysis + simulation sandbox (via OpenAI/Anthropic) that strategies must pass before deployment.
