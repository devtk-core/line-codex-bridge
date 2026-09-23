# Security Policy

## Reporting a vulnerability

Please use GitHub Private vulnerability reporting for security issues. Do not
include LINE credentials, OpenAI credentials, webhook payloads, repository
contents, or other secrets in a public issue.

## Deployment assumptions

- The webhook is exposed only through HTTPS.
- `LINE_ALLOWED_USER_IDS` contains only trusted operators.
- Group and room execution stays disabled unless its disclosure risk is accepted.
- `CODEX_WORKDIR` contains only files that Codex is allowed to inspect and edit.
- LINE credentials are stored outside the repository and are not inherited by
  the Codex child process.
- The service and Codex CLI run with the least filesystem privileges practical.
