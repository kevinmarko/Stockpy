# Walkthrough: feat-settings-census-gate

- Created E2 test `test_no_missing_call_timeouts.py` which searches `.py` files using the `ast` module for `subprocess.*` and `requests.*` calls to ensure they specify a `timeout` argument.
- Filtered out `investyo_mcp_server.py:343` and `scripts/build_command_manifest.py:136` as the two allowlisted exceptions.
- Appended `test_measure_settings_census_gate` in `test_measure_settings_census.py` to allowlist every currently-known field with the comment `# removed once WP-A/B/C/D lands`.
- Validated tests pass and regenerated census docs.
