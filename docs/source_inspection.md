# Source inspection, 2026-11-06

The official documentation was inspected through web browsing. Local Python downloads
were attempted separately and failed because the execution host has no outbound network
route (`WinError 10051`). Documentation access does not establish local dataset coverage.

| Source | Inspected location / adapter | Finding |
|---|---|---|
| FRED SP500 | https://fred.stlouisfed.org/series/SP500 | Daily close, roughly ten years of history; revisions possible. CSV adapter: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500`. |
| Cboe VIX | https://www.cboe.com/tradable-products/vix/vix-historical-data | Official page links `https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`. |
| Cboe VIX9D | Same volatility history page | Official page links `https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv`. The web reader reported unsupported CSV content type; local retrieval was network-blocked. |
| SPXW settlements | https://www.cboe.com/index_settlement_values/weeklys_settlement_values/ | Public dated table is identified; adapter filters explicit `SPXW` rows. Full historical download coverage remains unverified. |
| SET settlements | https://www.cboe.com/index_settlement_values/ | Official SET display exists. Complete dated historical coverage is unverified; no dates inferred from a monthly heading. Verified CSV import is required. |
| SPX specifications | https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/ | AM and PM contracts have different final trading/settlement conventions. A current page cannot establish all historical listing eligibility. |
| Cboe calendar | https://cdn.cboe.com/resources/options/Cboe2026OPTIONSCalendar.pdf | Dated official calendar found; historical calendar/exceptional closure transcription remains an explicit required import. |
| Cboe holidays | https://www.cboe.com/about/hours/us-options/ | Official hours/holiday starting point; no generic business-day calendar substitutes for official history. |

Implementation references: XGBoost 3.1 documentation at
https://xgboost.readthedocs.io/en/release_3.1.0/python/examples/quantile_regression.html
documents the quantile objective, histogram trees and possible crossings.
https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.QuantileRegressor.html
documents linear quantile regression. Model parameters are explicitly fixed in the package.

No web-visible observation excerpts were transcribed into notebook data. Raw request
snapshots and observation content hashes will be created only when a local download or
verified import actually succeeds. Failed attempts retain URLs, timestamps and errors in
the source manifest; an unavailable dataset has no invented content hash.
