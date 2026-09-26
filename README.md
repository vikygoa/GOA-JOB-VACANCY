# Goa Jobs

Automatic Goa job-post aggregator.

## Current test sources
- `jobs_update_in_goa`
- `dailyjobs__goa`

## Pipeline
Instagram API → download new post images → Gemini reads poster → structured job JSON → `jobs.json` → GitHub Pages.

## Setup

1. Create a GitHub repository and upload this project.
2. In **Settings → Secrets and variables → Actions**, create:
   - `GEMINI_API_KEY` = your Gemini API key
3. Do **not** put the API key in Python files.
4. Run **Actions → Daily Goa Jobs → Run workflow** manually for the first test.
5. The scheduled run is every day at 07:00 India time (01:30 UTC).

The Gemini model is configurable in `config.json`. If the configured model is unavailable, the workflow reports the API error instead of silently publishing bad data.

## Important
The Instagram endpoint used here is the endpoint supplied for this project. It is not an official Meta API. If it stops returning data, the scraper will fail safely and keep the existing `jobs.json`.
