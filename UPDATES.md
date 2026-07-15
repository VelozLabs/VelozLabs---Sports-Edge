# BaseballIQ Update Log

## Latest Updates (Phase 1 & 2)
### End-to-end Audit & Scrub
- Completed full end-to-end audit of the codebase.
- Stripped out previous developer callouts and authorship from the repository (e.g. LICENSE).
- Synchronized the architectural documentation (`README.md`, `ARCHITECTURE.md`) to reflect the actual state of the codebase.

### Phase 1: Gemini Layer Implementation
- **Swapped LLM Provider:** Stripped out Anthropic Claude dependencies and replaced the enrichment layer with Google Gemini 2.0 Flash (`google-genai`).
- **Cost Reduction:** Leveraging Gemini's free tier brings the daily operational cost to $0.
- **Dependencies:** Removed `anthropic` from all files and environment templates, successfully replaced by `google-genai`.

### Phase 2: High-Speed Static Dashboard Generation
- **Static Site Generator:** Created a high-speed Python site-builder script (`build_site.py`) leveraging `Jinja2` templates.
- **Ultra-Fast Landing Page:** Deprecated the heavy Streamlit frontend (kept as an option) in favor of a pre-rendered flat `docs/index.html` file.
- **Betting Specific Features:** Designed a dark-themed UI that highlights betting-specific statistics directly in a dynamic, sortable client-side table (CSW%, Whiff%, Velocity Delta, xwOBA).
- **Embedded AI Analyst:** Integrated the Gemini JSON outputs as hover-tooltips for an unobtrusive but powerful scouting feature.

### Phase 3: Daily CI/CD Setup
- **GitHub Actions Integration:** Added a daily workflow (`.github/workflows/daily_pipeline.yml`) to automatically pull Statcast data at 6 AM ET, rebuild DuckDB and the static UI, and push the artifact directly back to GitHub for free GitHub Pages hosting.
