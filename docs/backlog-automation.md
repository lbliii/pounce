# Backlog Acceptance Automation

Pounce links issue closure to executable acceptance proof. A pull request body
that uses a GitHub closing keyword such as `Closes #123` must provide one of:

- a test decorated with `@pytest.mark.issue(123)` that exercises the issue's
  acceptance criteria; or
- an explicit `Acceptance: n/a (reason)` line for work whose acceptance is not
  executable, such as a positioning-only or documentation-only change.

The exemption is an auditable exception, not a shortcut for untested runtime
behavior. Testable code changes still require a marker on the smallest test
that demonstrates the user-visible outcome.

## Local commands

```bash
python scripts/issue_coverage.py --issue 123
python scripts/issue_coverage.py --untested 123 124
python scripts/check_closure_acceptance.py --body "Closes #123"
```

`scripts/issue_coverage.py` scans test modules with Python's AST, so comments
and strings cannot fabricate coverage. Function-, class-, and module-level
markers are supported. The GitHub workflow supplies the pull request body to
`scripts/check_closure_acceptance.py`; neither script needs network access.
