import 'package:greenhouse_app/models/history_point.dart';

/// Renders [points] as CSV text: a header row followed by one row per point
/// (UTC ISO-8601 timestamp, avg, min, max). Values are left unformatted (no
/// unit suffix) so the file opens directly as numeric columns in a
/// spreadsheet -- unit/zone/range context lives in the filename instead
/// (see [historyCsvFilename]).
String historyToCsv(List<HistoryPoint> points) {
  final buffer = StringBuffer();
  buffer.writeln('time_utc,avg,min,max');
  for (final p in points) {
    buffer.writeln('${p.time.toUtc().toIso8601String()},${p.avg},${p.min},${p.max}');
  }
  return buffer.toString();
}

/// Builds a filesystem-safe filename for a CSV export, e.g.
/// `zone1_air_temperature_last_24_hours.csv` or
/// `weather_temperature_last_7_days.csv`.
String historyCsvFilename({
  required String kind,
  String? zone,
  required String metric,
  required String rangeLabel,
}) {
  final prefix = kind == 'weather' ? 'weather' : _slug(zone ?? kind);
  return '${prefix}_${_slug(metric)}_${_slug(rangeLabel)}.csv';
}

String _slug(String s) => s
    .toLowerCase()
    .replaceAll(RegExp(r'[^a-z0-9]+'), '_')
    .replaceAll(RegExp(r'^_+|_+$'), '');
