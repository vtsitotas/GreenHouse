import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/utils/history_csv.dart';

void main() {
  group('historyToCsv', () {
    test('emits header-only CSV for an empty list', () {
      expect(historyToCsv(const []), 'time_utc,avg,min,max\n');
    });

    test('emits one UTC-ISO8601 row per point, in avg,min,max order', () {
      final points = [
        HistoryPoint(
          time: DateTime.utc(2026, 8, 22, 12, 30),
          avg: 21.5,
          min: 20.0,
          max: 23.0,
        ),
        HistoryPoint(
          time: DateTime.utc(2026, 8, 22, 12, 31),
          avg: 21.7,
          min: 20.1,
          max: 23.2,
        ),
      ];
      final csv = historyToCsv(points);
      final lines = csv.trimRight().split('\n');
      expect(lines[0], 'time_utc,avg,min,max');
      expect(lines[1], '2026-08-22T12:30:00.000Z,21.5,20.0,23.0');
      expect(lines[2], '2026-08-22T12:31:00.000Z,21.7,20.1,23.2');
      expect(lines.length, 3);
    });

    test('converts local-time points to UTC before formatting', () {
      final localTime = DateTime(2026, 8, 22, 12, 30);
      final csv = historyToCsv([
        HistoryPoint(time: localTime, avg: 1, min: 1, max: 1),
      ]);
      final row = csv.trimRight().split('\n')[1];
      expect(row, '${localTime.toUtc().toIso8601String()},1.0,1.0,1.0');
    });
  });

  group('historyCsvFilename', () {
    test('uses "weather" as the prefix for weather queries (zone == null)', () {
      expect(
        historyCsvFilename(
          kind: 'weather',
          zone: null,
          metric: 'temperature',
          rangeLabel: 'Last 24 Hours',
        ),
        'weather_temperature_last_24_hours.csv',
      );
    });

    test('uses the zone as the prefix for zone queries', () {
      expect(
        historyCsvFilename(
          kind: 'zone',
          zone: 'zone1',
          metric: 'air_temperature',
          rangeLabel: 'Last 7 Days',
        ),
        'zone1_air_temperature_last_7_days.csv',
      );
    });

    test('slugifies a custom-range label with punctuation into a safe filename', () {
      expect(
        historyCsvFilename(
          kind: 'zone',
          zone: 'zone2',
          metric: 'soil_moisture',
          rangeLabel: 'Aug 1 – Aug 15',
        ),
        'zone2_soil_moisture_aug_1_aug_15.csv',
      );
    });
  });
}
