# History Chart Enhancement — Design Spec

**Date:** 2026-07-08
**Status:** Approved, ready for implementation planning

## Background

The history chart (`app/lib/screens/history/history_screen.dart`), shipped in the
sensor-database slice (`docs/superpowers/specs/2026-07-02-sensor-database-design.md`),
draws only a bare line via `CustomPainter` — no axis, no gridlines, no timestamps,
no way to switch metric or time range, and no way to view weather-metric history
at all (only zone metrics are reachable, and only the `air_temperature` metric,
since `ZoneCard` hardcodes the route). A prior session added a current-value
readout and min/max text labels on the chart, but the underlying chart is still
a single fixed-range, fixed-metric line with no real axis system.

This spec covers a full rebuild of the history-chart experience: real axes and
gridlines, a time-range selector, metric switching within a zone or for weather,
a min–max shaded band, and a short-range prediction overlay.

**Explicitly out of scope:** any other screen in the app (dashboard, control,
devices, pairing, settings, weather-screen forecast chart). The user's original
ask was "enhance the whole app"; this was deliberately scoped down to the
history chart as its own spec, with the rest of the app to be brainstormed
screen-by-screen in follow-up sessions. See the scope-decomposition discussion
in this spec's originating conversation.

## Goals

1. Replace the fixed-24h, fixed-metric chart with a real axis/grid chart
   (x = time, y = value, gridlines, tick labels).
2. Let the user switch time range (24h / 7d / 30d / 90d) without navigating away.
3. Let the user switch which metric is charted for a given zone (Temp / Humidity
   / Soil / Light) or for weather (Temp / Humidity / Wind / UV / Rain), without
   navigating away.
4. Add a weather-history entry point (currently there is none — only zone cards
   link to history).
5. Show a short predictive extension past "now": a real weather forecast for
   temperature/rain, a simple trend extrapolation for everything else.
6. Keep the existing current-value header (value + range) from the prior
   iteration, adapted to the selected time range.

## Non-goals

- No changes to the Pi/recorder/portal backend. `/api/history` and
  `/api/history/series` already support everything this spec needs
  (`kind`, `zone`, `metric`, `hours` params; automatic minute/hourly resolution
  switch at the 48-hour boundary).
- No fix for the pre-existing `pressure` metric gap (simulator publishes
  `greenhouse/weather/pressure`, but it isn't in the recorder's
  `_WEATHER_METRICS` set, so it's silently dropped and never recorded). Weather
  metric tabs in this spec are therefore Temp / Humidity / Wind / UV / Rain —
  no Pressure tab, since there is no data for it.
- No redesign of the weather screen's existing `_ForecastChart` (a separate,
  already-working, forward-looking-only chart). This spec only touches the
  shared history screen.
- No offline/caching layer for history data — same "just fetch on demand,
  show error/empty states" behavior as today.

## Data & Navigation Changes

- **`HistoryQuery`** (`app/lib/providers/history_provider.dart`) gains two
  fields: `kind` (`'zone'` | `'weather'`, defaults `'zone'` when `zone != null`)
  and `hours` (`double`, replaces the hardcoded `24`). Its existing `zone`
  field changes from `required String zone` to `String? zone`, since weather
  queries have no zone (mirrors how `_history_db`/`/api/history` on the Pi
  already treat `zone` as nullable via `zone IS ?`).
- **`ZoneCard`** (`app/lib/screens/dashboard/zone_card.dart`): each metric chip
  (Temp / Humidity / Soil / Light) becomes its own tappable target, routing to
  `/history/{zone}/{metric}` with that chip's specific metric — replacing the
  current whole-card tap that always routes to `air_temperature`.
- **`WeatherCard`** (`app/lib/screens/dashboard/weather_card.dart`): gains a tap
  target routing to a weather-history variant of the history route, e.g.
  `/history/weather/{metric}` (zone segment omitted/empty, `kind=weather`
  inferred from the route).
- **`app.dart`** router: add the weather-history route alongside the existing
  `/history/:zone/:metric` route (or extend the same route to accept a
  sentinel zone value like `weather` — implementation detail for the plan to
  decide, either is acceptable).
- **`HistoryScreen`** becomes metric-tab-aware: given an initial
  `(kind, zone?, metric)`, it renders a tab/chip row for the other metrics
  available for that `kind` (zone: Temp/Humidity/Soil/Light; weather:
  Temp/Humidity/Wind/UV/Rain) and swaps the active `HistoryQuery` on tab change
  — no navigation, just a new query into the same `FutureProvider.family`.
- A time-range chip row (24h / 7d / 30d / 90d) sits above the chart and drives
  the `hours` param the same way.

## Visual Design (fl_chart)

- **Library choice:** adopt the `fl_chart` package (pure Dart/Flutter, no
  native code — won't interact with the existing NDK-version build warning)
  and replace the hand-rolled `_HistoryPainter` with `fl_chart`'s `LineChart`.
- **X-axis (bottom):** timestamp labels, ~4–6 ticks regardless of range. Format
  adapts to the selected window: `HH:mm` for 24h; `"Mon 14:00"`-style for 7d;
  `"MMM d"` date-only for 30d/90d. No `intl` dependency — a small manual
  formatter (weekday/month name lookup arrays), consistent with how the
  existing `history_screen.dart` already avoids `intl`.
- **Y-axis (left):** value labels including the metric's unit (reusing the
  existing `_unitFor()` helper), gridline spacing computed from the loaded
  data's min/max range via fl_chart's `horizontalInterval`.
- **Grid:** light horizontal + vertical gridlines via fl_chart's `gridData`,
  theme-aware (subtle in both light and dark mode).
- **Series, three layers:**
  1. **Min–max band** — shaded region between each bucket's min and max, via
     fl_chart's `betweenBarsData` (replaces the old flat fill-under-avg-line).
  2. **Avg line** — solid, brand-colored (`AppColors.brandLight`), drawn above
     the band. This is the existing line, carried over as-is visually.
  3. **Prediction line** — starts exactly where the avg line ends, dashed,
     a muted variant of the brand color, extends into the future portion of
     the x-axis. A thin vertical "now" marker line separates history from
     prediction.
- **Tooltips:** tap-and-drag shows a popup with exact timestamp + avg/min/max
  for that bucket; on the predicted segment the popup reads e.g.
  `"predicted · 22.4°C"` instead, so it's never mistaken for a real reading.
- **Header** (from the prior iteration, kept): current value + min–max range
  for the period. The range label becomes dynamic (e.g. "Last 7 Days" instead
  of a hardcoded "Last 24 Hours"), reflecting the selected time-range chip.
- **Axis extension for prediction:** the x-axis spans ~20% past "now" beyond
  the historical data's end, to leave room for the dashed prediction segment.

## Prediction Logic

Two modes, chosen per metric:

- **Trend extrapolation** (default — soil moisture, light, wind, UV, humidity;
  also the fallback for temp/rain when forecast data is unavailable): a new
  pure helper, `predictTrend()`, does linear regression (least squares) over
  the last ~12 buckets of whatever resolution is currently loaded, then
  extrapolates forward at the same bucket spacing to fill the chart's future
  extension window. Percent-like/non-negative metrics (soil moisture,
  humidity, UV index) are clamped at a 0 floor on the extrapolated values —
  a single `.clamp(0, ...)` call, nothing more elaborate.
- **Forecast overlay** (temperature and rain, `kind == 'weather'` only): a new
  mapper, `predictFromForecast()`, reuses the existing `forecastProvider`
  (`app/lib/providers/connection_provider.dart`, fed by the MQTT-retained
  `greenhouse/weather/forecast` topic, already populated for the weather
  screen) and maps its `times[]` / `temps[]` / `precip[]` arrays into the same
  point shape the chart consumes (`avg = forecast value`, `min = max = avg`,
  since a forecast has no spread). Only as many forecast hours are drawn as
  fit within the chart's future extension window; when the extension is wider
  than the 24h of forecast available (true for 7d/30d/90d ranges), the dashed
  line simply stops when forecast data runs out — no extrapolation past it.
- **Fallback:** if the forecast stream has no data yet or errors, temperature
  and rain silently fall back to `predictTrend()` too — the prediction segment
  is never just absent because of a transient forecast hiccup.
- **Degenerate case:** fewer than 2 real data points in the loaded window →
  skip prediction entirely (can't fit a line to 0–1 points), render history
  only.
- **Where it lives:** `predictTrend()` and `predictFromForecast()` are pure,
  independently unit-testable functions (new file, e.g.
  `app/lib/utils/history_prediction.dart`). A new provider,
  `historyWithPredictionProvider` (wrapping the existing
  `historyPointsProvider`), combines real + predicted points so
  `HistoryScreen` stays a dumb presentation layer consuming
  `(actual points, predicted points)`.

## Error Handling

- **Per-tab/per-range isolation:** `HistoryQuery` already keys the
  `FutureProvider.family`, so switching a metric tab or time-range chip
  naturally gets its own loading/error/empty state with no shared bleed
  between tabs.
- **Empty state:** keep the existing "No history yet..." message, made
  range-aware (e.g. "No history yet for this metric in the last 7 days").
- **Forecast failures are silent:** if the forecast stream errors or hasn't
  produced data yet, the prediction segment is simply omitted — it never
  blocks or errors the main chart, since it's a supplementary overlay, not
  core data.
- **Existing error state** ("Could not load history...") is unchanged in
  structure, just now scoped to whichever tab/range is active.

## Testing

Scoped proportionally to a thesis project, not a production SLA:

- Unit tests for `predictTrend()` — synthetic increasing/flat/decreasing
  series, the 0-floor clamp, and the <2-point edge case.
- Unit tests for `predictFromForecast()` — JSON→points mapping, and
  truncation when forecast is shorter than the extension window.
- A light widget test on `HistoryScreen` covering: tab switch triggers a new
  fetch, range-chip switch triggers a new fetch, and empty/error/data states
  still render correctly.
- No backend/Pi changes in this spec, so no new Pi-side tests are needed.

## Follow-up (explicitly deferred)

- Screen-by-screen enhancement pass over the rest of the app (dashboard,
  control, devices, pairing, settings, weather screen), each as its own
  future spec.
- Fixing the dropped `pressure` weather metric (simulator publishes it,
  recorder doesn't record it) — noted here for visibility, not fixed as part
  of this spec.
