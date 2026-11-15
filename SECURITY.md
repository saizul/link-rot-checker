# Security policy

## Supported versions

Security fixes are made for the latest release. Please upgrade before reporting.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | yes       |

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
vulnerability reporting instead: open the **Security** tab of
https://github.com/saizul/link-rot-checker and choose **Report a
vulnerability**.

Include what you found, how to reproduce it and what an attacker could do with
it. You should get an answer within a week. Once a fix is released you will be
credited in the changelog unless you prefer otherwise.

Never include tokens, cookies or header values in a report. Replace private
domains with `example.com`.

## Scope

In scope:

- injection through crawled content into reports: HTML (cross-site scripting),
  Markdown, spreadsheet formulas in CSV files
- parsing of untrusted HTML, robots.txt, sitemaps and API answers (crashes,
  entity expansion, excessive memory or CPU use)
- leaking header values passed with `--header-env` into logs or reports
- the crawler leaving its scope (requesting pages it should only check, or
  ignoring robots.txt when it is enabled)

Out of scope: requests to internal addresses that a crawled page links to when
you scan from inside a private network (documented in the README; use
`--ignore`), and the availability of the Wayback Machine.
