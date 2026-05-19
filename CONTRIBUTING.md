# Contributing to BackupBuddy

BackupBuddy stores real people's irreplaceable files. Every contribution matters.
Read CLAUDE.md and SECURITY.md before writing any code.

---

## Git commit conventions

Every commit must follow this format:

```
type(scope): description in present tense, lowercase
```

### Types

| Type | When to use |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `security` | Security improvement |
| `config` | Configuration change |
| `test` | Test addition or fix |
| `docs` | Documentation only |
| `refactor` | Code restructuring, no behaviour change |
| `chore` | Tooling, deps, project setup |

### Scopes

`gatekeeper`, `agent`, `watcher`, `fragmenter`, `restore`, `lifeboat`, `cluster`,
`invite`, `verify`, `rebalance`, `gui`, `onboarding`, `config`, `db`

### Examples

```bash
git commit -m "feat(gatekeeper): add storage pool quota enforcement"
git commit -m "fix(watcher): handle symlinks in backup path validation"
git commit -m "security(lifeboat): switch to Argon2id for key derivation"
git commit -m "test(restore): add hash verification failure scenario"
git commit -m "docs(decisions): add ADR-016 phase 1 scope boundary"
```

**Rules:**
- Never `git add .` blindly — always review `git status` and `git diff --staged`
- Never commit `.env`, `*.key`, `*.cap`, `lifeboat.enc`, or any file with secrets
- Commit after every logical unit of work — do not batch unrelated changes

---

## Branch naming

```
feat/short-description
fix/short-description
security/short-description
```

---

## Task completion checklist

Run this before marking any task as done:

### Security
- [ ] SECURITY.md rules followed for every new function
- [ ] No secrets, keys, or passphrases written to disk or logs
- [ ] GUI binding verified (Tailscale interface only, not 0.0.0.0)
- [ ] All file paths validated with `os.path.realpath()` before use
- [ ] Storage pool exclusion enforced for any backup path logic
- [ ] All SQLite queries parameterized — no string formatting
- [ ] All inbound data validated with Pydantic before processing
- [ ] No Tahoe internals (FURL, cap, shares) in user-facing output

### Code quality
- [ ] All code and comments in English
- [ ] No unused imports or variables
- [ ] Error handling covers failure scenarios — no silent swallowing
- [ ] Background jobs log start, completion, and errors
- [ ] No hardcoded paths, ports, or configuration values

### Testing
- [ ] New logic has a corresponding test in `tests/unit/`
- [ ] Existing unit tests still pass

### Git
- [ ] Committed after this logical unit of work
- [ ] Commit message follows `type(scope): description` format
- [ ] `git status` reviewed — no unintended files staged
- [ ] No `.env` or secret files included

### Phase scope
- [ ] Task is within Phase 1 scope (`project-docs/roadmap.md → Phase 1`)
- [ ] No Phase 2 features implemented

---

## Running unit tests

```bash
# Activate the virtual environment first
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# Run all unit tests
pytest tests/unit/

# Run a specific test file
pytest tests/unit/test_config.py -v
```

Tests must not require network access or external services.
All Tahoe-LAFS and Tailscale interactions are mocked in unit tests.

---

## Dependency auditing

Run `pip-audit` before every release and as part of CI:

```bash
pip-audit
```

No high or critical vulnerabilities are acceptable. If a vulnerability is found,
update the affected package before continuing.

---

## Code language

All code, comments, variable names, and commit messages must be in English.
Conversations with the project owner are in Swedish.
