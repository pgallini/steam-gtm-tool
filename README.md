# steam-gtm-tool

The deployable web application entry point is `ui.app:app`. Production deployment
settings and required environment variables are documented in [DEPLOYMENT.md](DEPLOYMENT.md).

Pipeline logs are emitted as structured JSON to stdout/stderr so hosting platforms
can collect them without persistent local disk.

Step logger names map to the original manual script steps, for example:

- `01_extract_seed_page_signals.log`
- `02_discover_candidates.log`
- `03_get_app_details.log`
- `04_filter_candidates.log`
- `05_score_more_like_this.log`
- `06_shortlist_candidates.log`
- `07_llm_classify_comps.log`
- `08_generate_comp_report.log`
- `09_fetch_tier1_reviews.log`
- `10_summarize_tier1_reviews.log`
- `11_llm_rollup_review_insights.log`
- `12_generate_review_insights_report.log`
