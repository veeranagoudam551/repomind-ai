"""Unit tests for app.services.security_scan.scan_content.

Deliberately not LLM-backed (see module docstring), so these are plain
regex-behavior tests rather than mocked API calls like every other AI
service's test file.
"""

from app.services.security_scan import scan_content


def test_scan_content_detects_hardcoded_secret():
    findings = scan_content('password = "supersecretpass123"\n')
    assert any(f.rule_id == "hardcoded-secret" for f in findings)
    assert findings[0].line == 1
    assert findings[0].severity == "high"


def test_scan_content_detects_prefixed_hardcoded_secret():
    # DB_PASSWORD / MY_API_KEY-style names have no word boundary right
    # before "password"/"api_key" (the underscore is itself a word
    # character), so the rule must match through prefixes/suffixes too.
    findings = scan_content("DB_PASSWORD = 'reallysecretpass123'\n")
    assert any(f.rule_id == "hardcoded-secret" for f in findings)


def test_scan_content_detects_private_key():
    content = "-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n-----END RSA PRIVATE KEY-----\n"
    findings = scan_content(content)
    assert any(f.rule_id == "private-key" for f in findings)


def test_scan_content_detects_eval_exec():
    findings = scan_content("result = eval(user_input)\n")
    assert any(f.rule_id == "eval-exec" for f in findings)


def test_scan_content_detects_shell_injection():
    findings = scan_content("subprocess.run(cmd, shell=True)\n")
    assert any(f.rule_id == "shell-injection" for f in findings)


def test_scan_content_detects_insecure_deserialization():
    findings = scan_content("data = pickle.loads(raw)\n")
    assert any(f.rule_id == "insecure-deserialization" for f in findings)


def test_scan_content_detects_unsafe_yaml_load():
    findings = scan_content("config = yaml.load(stream)\n")
    assert any(f.rule_id == "unsafe-yaml-load" for f in findings)


def test_scan_content_allows_safe_yaml_load():
    findings = scan_content("config = yaml.load(stream, Loader=yaml.SafeLoader)\n")
    assert not any(f.rule_id == "unsafe-yaml-load" for f in findings)


def test_scan_content_detects_sql_string_interpolation():
    findings = scan_content('query = f"SELECT * FROM users WHERE id = {user_id}"\n')
    assert any(f.rule_id == "sql-string-interpolation" for f in findings)


def test_scan_content_detects_unsafe_html_injection():
    findings = scan_content("el.innerHTML = userSuppliedHtml;\n")
    assert any(f.rule_id == "unsafe-html-injection" for f in findings)


def test_scan_content_reports_correct_line_numbers():
    content = "line one\nline two\npassword = 'longenoughsecret'\nline four\n"
    findings = scan_content(content)
    assert len(findings) == 1
    assert findings[0].line == 3
    assert findings[0].snippet == "password = 'longenoughsecret'"


def test_scan_content_returns_empty_for_clean_code():
    findings = scan_content('def greet(name):\n    print(f"Hello, {name}!")\n')
    assert findings == []
