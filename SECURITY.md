# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 2.0.x   | Yes       |
| < 2.0   | No        |

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

Instead, report them privately using GitHub Security Advisories:

- **Private Vulnerability Reporting:** https://github.com/Masaru-urasaM/CrossTrans2/security/advisories/new

Please include as much of the following as you can:
- CrossTrans 2 version (found in **Settings > About**) and Windows version
- Steps to reproduce or a proof of concept
- Affected feature or component (e.g., API key storage, auto-update, clipboard handling, audio recording)
- Any suggested mitigation or patch

We aim to acknowledge reports within 72 hours and provide an update on the investigation and planned fix shortly after.

## Security & Privacy Architecture

CrossTrans 2 is a local-first Windows desktop application designed to minimize data exposure:

- **Credential Protection:** AI API keys and optional GitHub update tokens are encrypted per-user with Windows DPAPI (`CryptProtectData`). Viewing saved API keys in Settings requires native Windows user verification (Windows Hello / PIN / password).
- **Direct Provider Connections:** Translations, OCR, and speech requests are sent directly from your PC to the AI provider(s) you configure. CrossTrans 2 does not operate a proxy server that stores your text, screenshots, or recordings.
- **Strict Log Sanitization:** Diagnostic logs and exported bug reports automatically exclude API keys, tokens, and user translation text/history.
- **Verified Auto-Updates:** Update checks connect to GitHub Releases over HTTPS, verify the downloaded installer's SHA-256 digest before launch, and strip `Authorization` headers on cross-origin redirects.
- **Local-Only History & Recordings:** Translation history is stored locally on your machine and can be disabled or cleared at any time. Audio recordings are temporary unless you choose to save them.
