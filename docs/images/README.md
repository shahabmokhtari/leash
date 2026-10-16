# Documentation screenshots

These PNGs show **the repository's actual HTML, CSS, and JavaScript**, rendered in headless Microsoft Edge. API responses are synthetic fixtures for a fictional `sample-store` project. Scores, counts, latency, token usage, session text, and hook status are illustrative, not production measurements or default settings.

The capture does not run the Leash backend, install hooks, contact an LLM, or read a user's configuration or transcripts. Playwright intercepts all page requests at the reserved `leash-docs.test` origin. Unexpected requests and JavaScript errors fail the capture.

| Image | View |
|-------|------|
| `dashboard.png` | Dark theme, profile and mode controls, hooks, counters |
| `insights.png` | Latency, recommendations, charts, per-tool statistics |
| `live-logs.png` | Filters and an expanded analysis |
| `sessions.png` | Claude/Copilot sessions, a subagent, and token usage |
| `transcript.png` | Conversation and side-by-side Edit diff |
| `settings.png` | LLM provider and persistent-process settings |
| `handlers.png` | Hook handler and threshold configuration |
| `prompts.png` | Prompt template and test form |

## Refresh the images

From the repository root, with Python, uv, and Microsoft Edge installed:

```bash
uv run --with playwright python docs/capture_screenshots.py
```

The browser automation package is a documentation-only tool, not a Leash runtime dependency. The script uses the already installed Edge browser, so no Playwright browser download is needed. For installed Google Chrome, pass `--channel chrome`.

The script writes these eight files under `docs/images/`, overwriting the previous captures. It uses a new browser context, fixed viewport sizes, UTC timestamps, and an English locale, and closes the browser on completion or failure. It does not start a web server or use your normal browser profile. Font rendering can differ across operating systems.

After capture, inspect every image for readable text, correct framing, and sensitive data before committing. If a frontend API or selector changes, update the fixture/assertion rather than hiding the error or substituting a live backend.
