# Test Plan: Research a Specific Steam Game

## Purpose

Demonstrate the intended user path for running Steam GTM research on a specific Steam game, and identify where the current Candidate Controls UI is confusing.

## Test Scenario

A user wants to run competitor/GTM research for one known Steam game.

Example seed game:

- Game: `Creepy Horrors: Blood Cult 2`
- Steam App ID: `4405120`
- Existing seeded run: `Blood Cult 2 Candidate Control Run`
- Existing run ID: `d9c6c6f6-3a74-46f5-9019-40c0d8af5b8e`

## Preconditions

- Local UI server is running at:
  - `http://127.0.0.1:8000/candidate_controls.html`
- `.env` contains:
  - `SUPABASE_URL`
  - `SUPABASE_ANON_KEY`
  - `SUPABASE_SERVICE_ROLE_KEY`
  - `OPENAI_API_KEY`
- Supabase contains at least one organization, one game, and one research run.
- The user has a Steam App ID for the game they want to research.

## Happy Path Test

### 1. Open the Candidate Controls UI

Open:

```text
http://127.0.0.1:8000/candidate_controls.html
```

Expected:

- Page loads with the title `Candidate Controls`.
- The `Run ID` field is visible.
- Buttons are visible:
  - `Load Run`
  - `List Runs`
  - `Refresh Runs`
  - `Generate Run Candidates`
  - `Run Full Pipeline`
  - `Refresh Reports`

Current UX issue:

- The page starts with a raw `Run ID`, not a clearer `Choose a game to research` flow.
- It is not obvious that a user must first select or create a research run before adding controls or running research.

### 2. Select an Existing Research Run

Use the seeded run for this test.

In `Run ID`, enter:

```text
d9c6c6f6-3a74-46f5-9019-40c0d8af5b8e
```

Click:

```text
Load Run
```

Expected:

- Run summary appears.
- Status and stage are displayed.
- Candidate controls table loads.
- Candidate summary table loads.
- Buttons become enabled:
  - `Save Control`
  - `Generate Run Candidates`
  - `Run Full Pipeline`
  - `Refresh Reports`

Current UX issue:

- A normal user likely will not know a run UUID.
- `List Runs` depends on choosing an organization, but the organization selector is also not framed as step 1.

### 3. Add Required Candidate Controls

Add a known competitor or benchmark.

Example:

| Field | Value |
|---|---|
| Title | Aethermancer |
| Steam App ID or URL | `2288470` |
| Control Type | `require_include` |
| Reason | Required direct competitor for creature-collector positioning |
| User Notes | Include in final competitor analysis |

Click:

```text
Save Control
```

Expected:

- Control is saved.
- It appears in the Candidate Controls table.
- Candidate summary refreshes.

Repeat with additional examples if desired:

| Title | Steam App ID | Control Type | Purpose |
|---|---:|---|---|
| Cult of the Lamb | `1151340` | `must_consider` | Strategic audience comparison |
| Graveyard Keeper | `289070` | `benchmark_only` | Commercial/scope benchmark |

Current UX issue:

- The meaning of control types is not explained in the UI.
- A user may not understand the difference between `require_include`, `must_consider`, `benchmark_only`, and `watchlist`.

### 4. Generate Run Candidates

Click:

```text
Generate Run Candidates
```

Expected:

- User-supplied controls are converted into `run_candidates`.
- Candidate Summary table updates.
- Run stage advances through the candidate preparation/discovery step.

Current UX issue:

- It is unclear whether this is the final research run or only a preparation step.
- The label could be interpreted as “run all research,” but it only prepares/generates candidates from controls.

### 5. Run the Full Research Pipeline

Click:

```text
Run Full Pipeline
```

Expected:

The pipeline runs through:

1. Candidate preparation
2. Steam enrichment
3. More Like This discovery
4. Scoring
5. LLM competitor classification
6. Competitor report generation
7. Steam review collection
8. LLM review insight generation
9. Review rollup
10. Review insights report generation

Expected completion result:

- Run status becomes `completed`.
- Run stage becomes `completed`.
- Candidate Summary contains classifications such as:
  - `direct_comp`
  - `adjacent_comp`
  - `audience_comp`
  - `commercial_benchmark`
- Reports are generated.

Current UX issue:

- This is probably the primary action users want, but it is placed beside lower-level utility buttons.
- There is no progress indicator by stage.
- Long-running work gives minimal feedback.

### 6. Review Candidate Summary

Inspect the Candidate Summary table.

Expected:

Each candidate row should show:

- Title
- Steam App ID
- Control Type, if user-supplied
- Pipeline Status
- Required / Excluded / Benchmark flags
- Notes/reasoning from classification

Pass criteria:

- Required candidates are present.
- Benchmark-only candidates are marked as benchmarks.
- Discovered candidates appear alongside user-supplied controls.
- LLM classifications are visible in the reasoning/notes field.

Current UX issue:

- The table does not clearly separate:
  - user-supplied candidates,
  - discovered candidates,
  - selected-for-report candidates,
  - ignored/noisy candidates.
- The table does not show ranking or score prominently.

### 7. Review Generated Reports

Scroll to:

```text
Generated Reports
```

Expected:

At least two report types are available:

- `competitor_report`
- `review_insights_report`

Open each report disclosure.

Expected:

- Competitor report includes selected candidates and classification rationale.
- Review insights report includes shared praise themes, complaint themes, and positioning opportunities.

Current UX issue:

- Reports are shown as raw markdown in collapsible blocks.
- There is no clear “final output” or “download report” call-to-action.

## Pass Criteria

The test passes if:

- A user can load an existing run for a specific Steam game.
- A user can add at least one candidate control.
- A user can generate candidates.
- A user can run the full pipeline.
- The run completes successfully.
- Candidate classifications are generated using OpenAI.
- Review insights are generated using OpenAI.
- Competitor and review insight reports are visible in the UI.

## Fail Criteria

The test fails if:

- The user cannot determine what to do first.
- The run cannot be loaded without knowing hidden UUIDs.
- Candidate controls save but do not appear in summary.
- `Run Full Pipeline` fails or leaves the run in a failed state.
- Reports are generated in Supabase but not visible in the UI.
- The UI does not make it clear what the final output is.

## Main UX Findings From This Test

The typical user path should probably be:

```text
Choose organization → Choose/create game → Enter seed Steam App ID → Create research run → Add required/excluded/benchmark comps → Run full research → Review candidates → Review reports
```

But the current UI path is closer to:

```text
Know or find a run UUID → Load run → Add controls → Generate candidates → Run full pipeline → Inspect raw tables/reports
```

This means the current UI is functionally useful for development/admin testing, but confusing for a normal user trying to research a specific Steam game.

## Recommended UI Improvements

1. Rename the page from `Candidate Controls` to something like `Steam GTM Research Run`.
2. Add a top-level stepper:
   - Step 1: Select or create game
   - Step 2: Add candidate controls
   - Step 3: Run research
   - Step 4: Review candidates
   - Step 5: Read/export reports
3. Replace raw UUID-first flow with game-first flow.
4. Add field for seed Steam App ID when creating a game/run.
5. Explain candidate control types inline.
6. Make `Run Full Pipeline` the primary CTA.
7. Add stage progress and error display from `run_events`.
8. Add report download/copy buttons.
9. Separate candidate summary into tabs or filters:
   - Required
   - Benchmarks
   - Discovered
   - Selected for report
   - Excluded/noise
10. Show final report outputs more prominently than internal control tables.
