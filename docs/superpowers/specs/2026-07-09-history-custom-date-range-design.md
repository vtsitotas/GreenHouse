# History Custom Date-Range Picker — Design Spec

**Date:** 2026-07-09
**Status:** Approved, ready for implementation planning

## Background

The history chart (`app/lib/screens/history/history_screen.dart`, built out in
`docs/superpowers/specs/2026-07-08-history-chart-enhancement-design.md`) only
supports rolling windows measured back from "now": 24h / 7d / 30d / 90d. There
is no way to view a specific past date or an arbitrary past range (e.g. "what
did zone 1 look like on July 3rd?" or "show me the week I was away"). The
recorder already retains the data to answer this (90 days at minute
resolution, 2 years at hourly rollup — `pi/scripts/recorder.py`), the gap is
purely in the query API and the UI.

Both existing history query paths — `portal.py`'s HTTP `/api/history` (LAN)
and `recorder.py`'s MQTT request/response mirror (used when connected via
HiveMQ Cloud, see `docs/superpowers/specs/2026-07-08-history-chart-enhancement-design.md`'s
predecessor spec) — only accept a relative `hours` parameter (window back from
`time.time()`) and are hand-maintained as two separate copies of the same
query logic.

## Goals

1. Let the user pick an arbitrary past date range (including a single day) in
   the History screen and see the real recorded data for it.
2. Support this over both the LAN (HTTP) and remote (MQTT) history paths,
   consistent with how every other range already works.
3. Remove the backend query-logic duplication between `portal.py` and
   `recorder.py` while touching both anyway for this feature.

## Non-goals

- No change to the existing rolling-window chips' behavior or API — `hours`
  keeps working exactly as it does today, for backward compatibility.
- No prediction/forecast overlay for custom ranges (see Prediction Handling
  below) — this is a real-data-only view.
- No change to data retention windows (90 days minute / 2 years hourly). The
  date picker is bounded to what's retained (see UI section) rather than the
  backend being changed to keep more history.
- No redesign of the chart rendering itself (axes, gridlines, tooltips) —
  reuses the existing `_HistoryChart` as-is; only the label formatting for the
  header/axis adapts to a custom range, same mechanism as the existing
  per-range formatting.

## Backend Changes

**Shared query module:** extract the query logic currently duplicated between
`portal.py`'s `history()` route and `recorder.py`'s `_query_points()` into one
shared function, `query_points(conn, kind, zone, metric, *, hours=None,
since=None, until=None)`, in a new shared module (e.g.
`pi/scripts/history_query.py`). Both `portal.py` and `recorder.py` import and
call it instead of keeping their own copy.

Resolution/table selection keeps today's rule — span `<= 48h` uses the
per-minute `readings` table, otherwise the hourly `readings_hourly` rollup —
just computed from `(until - since)` when those are provided, instead of
`(now - hours)`. Passing `since`/`until` overrides `hours` when both are
present; passing only `hours` behaves exactly as today.

**HTTP transport:** `/api/history` gains optional `since`/`until` query
params (unix epoch seconds, matching the existing `ts` column's units).

**MQTT transport:** the history-request JSON (`greenhouse/history/request`)
gains optional `since`/`until` fields alongside the existing `hours`,
handled by the same shared `query_points()` call.

## App Changes

- **`HistoryQuery`** (`app/lib/providers/history_provider.dart`) gains
  nullable `since`/`until` (`DateTime?`) fields, included in `==`/`hashCode`.
  When set, they take precedence over `hours` for the query (mirrors the
  backend's override rule).
- **`HistoryService`** (HTTP) and the MQTT request builder
  (`fetchHistoryViaMqtt`) pass `since`/`until` through as epoch seconds when
  present.
- **`history_screen.dart`**: a 5th chip, **"Custom…"**, added to the existing
  24h/7d/30d/90d row.
  - Tapping it opens `showDateRangePicker`, bounded `firstDate = now - 2
    years` (matching hourly-rollup retention, so users aren't offered dates
    guaranteed to be empty) and `lastDate = today`.
  - Confirming a range sets `_since` = start of the first picked day (local
    midnight), `_until` = end of the last picked day (local 23:59:59
    inclusive), and rebuilds the active `HistoryQuery` with those instead of
    `_hours`.
  - The chip's own label updates to show the picked range once set (e.g.
    `"Jul 3"` for a single day, `"Jul 1 – Jul 5"` for a span) instead of a
    generic "Custom…" — tapping the chip again while selected reopens the
    picker to change dates.
  - `_rangeLabel` and `_axisTimeLabel` extend their existing threshold logic
    (currently keyed off `_hours`) to key off the effective span
    (`until - since` when custom, `_hours` otherwise), reusing the same
    formatting rules already in place for 24h/7d/30d/90d.

## Prediction Handling

`historyWithPredictionProvider` skips its forecast/trend branch entirely when
`since`/`until` are set on the query, always returning `predicted: []`.
Rationale: the existing prediction feature extrapolates forward from "now" —
projecting a trend forward from an arbitrary past end-date isn't a real
prediction (the actual outcome already happened and isn't being forecast), so
custom-range views show only the real recorded data, no dashed overlay.

## Error Handling

- Relies on the existing empty-state message ("No history yet for this
  metric...") for date ranges with no data — the range-label change already
  makes this message describe the custom range correctly, no new empty-state
  UI needed.
- The picker's `firstDate` bound (2 years back) prevents most
  guaranteed-empty selections up front; anything still empty within that
  bound (e.g. a gap before the recorder was first deployed) falls through to
  the same empty state as any other range.
- Existing "Could not load history..." error state is unchanged in
  structure, just reachable from the Custom chip's query the same way as any
  other chip's.

## Testing

Scoped proportionally to a thesis project:

- `pi/tests/test_recorder.py` and `pi/tests/test_portal_history.py` (both
  currently cover this query logic separately, now pointed at the shared
  module): since/until query cases, and the resolution-boundary behavior
  (`<=48h` vs `>48h`) computed from `until - since` instead of `hours`.
- `app/test/providers/history_provider_test.dart`: `HistoryQuery` equality/
  hash with the new fields, since/until passed through to the HTTP/MQTT
  fetch calls, and prediction skipped when since/until are set.
- `app/test/widgets/history_screen_test.dart`: Custom chip opens the picker,
  picking a range updates the chip label and triggers a new fetch, range
  label/axis formatting for a custom span.

## Follow-up (explicitly deferred)

- Nothing specific identified beyond the existing app-wide backlog
  (screen-by-screen UX pass, ML watering prediction, etc. — see
  `HANDOFF.md`).
