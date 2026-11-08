# Verified official data imports

Put CSV files in `data/imports/`. These are input contracts, not synthetic data templates.
Every row must have a traceable FRED or Cboe `source_url`. Preserve the original official
download alongside your verification notes. Imports are snapshotted and hashed on ingestion.
Do not describe a transcription as verified unless you checked its dates and values against
the cited official record. No official historical calendar/SET archive is bundled.

## Manual daily-price downloads

Save unchanged official downloads as `SP500.csv`, `VIX_History.csv` and
`VIX9D_History.csv` here. These files take precedence over automatic price downloads,
including with `refresh=True`; replace or remove them to change that choice.
The importer records the expected official URL, user-supplied provenance, content hash
and first ingestion receipt for each distinct file. It does not independently verify
the download origin or treat the file modification time as publication evidence.

## `calendar.csv`

The supplied annual PDFs have now been visually reviewed and transcribed for 2016–2026.
Run `.\.venv\Scripts\python scripts/build_calendar_import.py` to regenerate this file;
add `--render` to regenerate page PNGs/text under `.runtime/calendar_review/pages/`
(requires PyMuPDF in the active environment). The reviewed PDF hashes, holiday dates,
standard monthly markings and exceptional closure notices are pinned in
`docs/calendar_review.json`. A changed PDF requires renewed visual review.

This is a **regular-session existence calendar**, not an intraday trading-hours schedule.
Early-close days remain sessions. December 5, 2018 and January 9, 2025 are closed for
regular trading according to separate Cboe notices. Source URLs point to the original
Cboe documents; Internet Archive retrieval URLs are retained separately for 2016–2019.
The generated `artifacts/calendar_validation.json` records the source hashes and
daily-price reconciliation; `artifacts/standard_monthly_expirations.csv` retains 132
visually transcribed standard monthly expiration dates. All 82 explicitly dated SET
records in the supplied monthly archive agree with these markings. The generated
monthly-date lookup is evidence for resolving dates, not a weekly eligibility schedule.
The contract builder below uses these dates for standard monthly AM contracts; the
settlement importer now joins older month-only values to that verified monthly schedule.

The VIX CSV includes 33 positive observations on regular-session closure dates within
the reviewed range. Those rows are disclosed in the validation receipt, retained in raw
data and excluded by regular-session feature reindexing; they do not create RTH sessions.
Cboe's GTH rules and holiday schedules explain why VIX can be observed without RTH.

Columns: `date,is_session,source_url`. One row **per calendar day**, including weekends and
closures; dates in ISO format; `is_session` is `true`/`false`. Include early-close days as
sessions. Cover the feature warm-up through at least the next regular session after the
latest expiration. Consult dated Cboe calendars and circulars for exceptional closures;
current holiday rules cannot establish historical eligibility. This complete calendar
determines session lags, holiday shifts, remaining sessions and availability cutoffs.

## `contracts.csv`

Generate this file with `.\.venv\Scripts\python -m scripts.build_contract_import`.
The default as-of date is the configured end date (currently 2026-11-06); use `--as-of`
to change it. The builder verifies reviewed source hashes and uses the policy in
`docs/contract_review.json`, calendar markings and explicit SPXW settlement responses.
It does not require a licensed strike-by-strike chain archive to classify settlement
targets, and it does not claim historical liquidity or exact listing timestamps.

The current file contains **695 period rows**: **377 eligible EOW PM dates**, **130
eligible standard monthly AM dates**, **130 standard-monthly collisions** excluded from
the EOW target and **58 month-end collisions** likewise excluded. Quarter-end collisions
are a subset of month-end. Daily, third-Friday PM and month-end SPXW contracts are
distinct from this model's EOW target, even when the symbol and settlement date coincide.
An excluded row does not assert that no PM contract existed.

Every included historical EOW date must have a matching explicit archive label. Missing
labels raise an error rather than being marked ineligible. Future dates are not inferred
from rules alone. The first two nominal weeks of 2016 need reference-session dates outside
the calendar and are disclosed in `artifacts/contract_validation.json`, rather than assigned
invented eligibility. The supported weekly span starts 2016-01-15. Required complete
evaluation years (2017–2025) have no missing contract periods or settlement labels.

Primary classification evidence includes SEC release 34-62911 (2010, footnote 5),
Cboe's annual calendars, the supplied nonstandard-expiration report, and Cboe circular
RG17-054, which distinguishes third-Friday PM SPXW from EOW after the 2017 conversion.
The 2021 report has stale 2019 pilot-expiry boilerplate, so it is not treated as sole
proof of eligibility. Cboe's 2024 dated notice supplies three independent EOW examples.
Sources, scope and limitations are recorded in `docs/contract_review.json`.

Columns: `horizon,nominal_expiration,expiration,settlement_type,eligible,source_url`.
Use horizons `weekly`/`monthly`, and types `SPXW_PM`/`SET_AM` respectively. Supply one
weekly row for **every nominal Friday**, with explicit `eligible=false` for weeks without
an eligible listed end-of-week contract. Supply one standard monthly row per month,
including the preceding month needed to construct the first monthly reference. The actual
`expiration` is the officially adjusted session, not an automatically computed third Friday.
For excluded weeks keep the nominal Friday in `expiration`; it is not used as a session.
End-of-month/daily PM contracts are not automatically end-of-week SPXW contracts.

Weekly references come from the prior calendar week's final verified regular session.
Monthly references come from the preceding month's listed standard AM settlement session,
regardless of whether its SET label is available. Cross-check historical listing eligibility
with contemporaneous Cboe specifications/circulars. Current rules alone are insufficient.

## `settlements.csv`

The supplied `cboe-weeklys-settlement-values-all-years.zip` is also read automatically.
Its `manifest.json` must identify year-specific Cboe `/get_sv_data/W/<year>/` sources
and JSON members containing either dated HTML tables or structured records. Only explicit
SPXW dates in the configured range are imported. The archive and each selected response
are snapshotted and hashed; provenance is user-supplied, not independently downloaded.
Nonnumeric/annotated HTML values are excluded into `artifacts/weekly_archive_review.csv`.
Per-year counts are in `artifacts/weekly_archive_coverage.csv`. Daily, end-of-month and
end-of-week SPXW rows can coexist; importing a label does not establish contract eligibility.
This archive does not supply standard monthly SET AM labels or missing calendar history.

`cboe-monthly-set-am-history.zip` is also read automatically. Raw `source/<year>.json`
members control values, rather than the supplied derived summary. Structured records with
trading symbol SPX and settlement symbol SET supply explicitly dated monthly AM labels.
Older HTML values under month headings are initially retained without guessed dates.
When verified calendar and standard AM contract imports are available, the importer
joins by month and preserves both the value-source hash and date-source PDF/import hashes.
The 82 explicitly dated labels (2020–October 2026) plus **48 resolved values (2016–2019)**
now supply **130 monthly labels**. The remaining **12 month-only values from 2015** stay
in `artifacts/monthly_archive_undated.csv`, outside the supported calendar and required
training/testing years. They do not block the required split. Resolution evidence is in
`artifacts/monthly_archive_date_resolutions.csv`; original source-schema coverage remains
in `artifacts/monthly_archive_coverage.csv`.

`cboe-historical-calendars-and-listing-records.zip` is a reference-document bundle.
Its calendar manifest covers 2020–2026; the README explicitly disclaims complete
historical contract eligibility. Together with `cboe-options-calendars-2016-2019.zip`
and the pinned visual review, it supplies the calendar-generation script above.
It does not automatically create `contracts.csv`. Current HTML snapshots cannot
establish historical listings.

Required columns: `horizon,expiration,settlement_type,settlement,source_url`.
Optional: `published_at` as timezone-aware ISO timestamp (blank if unknown).
Use positive, official Cboe **SET** AM values for monthly labels and official SPXW PM
records for weekly labels. Never use the FRED close as a settlement label. Records join
by horizon, expiration and type, with unique keys. Conflicting duplicates fail validation.
Exact duplicate official values from the current web table/import collapse once.

The public weekly HTML adapter accepts explicitly dated SPXW rows only. It downloads the
currently served page; it does not claim to discover an unverified multi-year endpoint.
The monthly page often identifies a month without an unambiguous settlement date; it is
retained as a raw source snapshot and requires verified dated imports. You may accumulate
official records in the CSV over time. Full history is checked before a horizon runs.

## `publications.csv` (optional)

Columns: `dataset,date,published_at,source_url`, with dataset `spx`, `vix`, or `vix9d`.
Use a verified timezone-aware observation publication timestamp. File modification and
retrieval timestamps do not prove original publication. Without it, backtests assume
availability by the following session 08:00 America/New_York; live issues conservatively
use receipt time. Unknown settlement timestamps become trainable at 00:00 on the following
regular session. Consequently an old snapshot retrieved after a cutoff cannot prove timely
live receipt, even though its historical backtest can use the disclosed assumption.

## Completeness and pending states

The split uses complete expiration years. Five initial years, two or more development
years and the latest two complete years before configured `end` are mandatory. Missing
contract periods, official calendar dates, positive daily inputs, feature windows or
settlement labels leave that horizon pending. Unknown calendar missing-session counts are
reported as unknown, never zero. No weekday or close-price fallback is enabled.
