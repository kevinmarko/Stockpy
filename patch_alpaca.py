with open("tests/test_alpaca_http.py", "r") as f:
    content = f.read()

# For the first failing test
content = content.replace(
    'session = requests.Session()\n            mount_timeout_adapter(session, 30.0)',
    'session = requests.Session()\n            session.trust_env = False\n            mount_timeout_adapter(session, 30.0)'
)
# For the second failing test
content = content.replace(
    'session = requests.Session()\n            mount_timeout_adapter(session, 0.3)',
    'session = requests.Session()\n            session.trust_env = False\n            mount_timeout_adapter(session, 0.3)'
)
# Also patch TestTimeoutHTTPAdapterUnit.test_injects_default_timeout_when_caller_omits_it_entirely
content = content.replace(
    'session = requests.Session()\n            mount_timeout_adapter(session, 0.2)',
    'session = requests.Session()\n            session.trust_env = False\n            mount_timeout_adapter(session, 0.2)'
)

with open("tests/test_alpaca_http.py", "w") as f:
    f.write(content)
