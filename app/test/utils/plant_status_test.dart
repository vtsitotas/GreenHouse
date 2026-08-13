import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/plant_profile.dart';
import 'package:greenhouse_app/theme/app_colors.dart';
import 'package:greenhouse_app/utils/plant_status.dart';

void main() {
  group('metricStatus', () {
    const range = ClimateRange(min: 40, max: 60);

    test('bounds are inclusive -- exactly at min or max is good', () {
      expect(metricStatus(40, range), PlantConditionStatus.good);
      expect(metricStatus(60, range), PlantConditionStatus.good);
      expect(metricStatus(50, range), PlantConditionStatus.good);
    });

    test('below min is low, above max is high', () {
      expect(metricStatus(39.9, range), PlantConditionStatus.low);
      expect(metricStatus(60.1, range), PlantConditionStatus.high);
    });

    test('null value is unknown, not a silent zero', () {
      expect(metricStatus(null, range), PlantConditionStatus.unknown);
    });

    test('a range with neither bound is unknown -- nothing to compare against', () {
      expect(metricStatus(50, const ClimateRange()), PlantConditionStatus.unknown);
    });

    test('a one-sided range (no floor) only checks the side that exists', () {
      const ceilingOnly = ClimateRange(max: 80);
      expect(metricStatus(1, ceilingOnly), PlantConditionStatus.good);
      expect(metricStatus(81, ceilingOnly), PlantConditionStatus.high);
    });
  });

  group('overallZoneStatus', () {
    test('worst wins: low or high beats unknown beats good', () {
      expect(
        overallZoneStatus({'a': PlantConditionStatus.good, 'b': PlantConditionStatus.low}),
        PlantConditionStatus.low,
      );
      expect(
        overallZoneStatus({'a': PlantConditionStatus.good, 'b': PlantConditionStatus.unknown}),
        PlantConditionStatus.unknown,
      );
      expect(
        overallZoneStatus({'a': PlantConditionStatus.high, 'b': PlantConditionStatus.unknown}),
        PlantConditionStatus.high,
      );
    });

    test('all good is good', () {
      expect(
        overallZoneStatus({'a': PlantConditionStatus.good, 'b': PlantConditionStatus.good}),
        PlantConditionStatus.good,
      );
    });

    test('no metrics at all is unknown', () {
      expect(overallZoneStatus(const {}), PlantConditionStatus.unknown);
    });
  });

  group('zoneStatusVisual', () {
    test('every status maps to a distinct, non-null icon', () {
      final icons = PlantConditionStatus.values.map((s) => zoneStatusVisual(s).$2).toSet();
      expect(icons.length, PlantConditionStatus.values.length);
    });

    test('low and high share the warning colour -- both mean "do something"', () {
      expect(zoneStatusVisual(PlantConditionStatus.low).$1, AppColors.warning);
      expect(zoneStatusVisual(PlantConditionStatus.high).$1, AppColors.warning);
    });

    test('good is the online colour, unknown is the pending colour', () {
      expect(zoneStatusVisual(PlantConditionStatus.good).$1, AppColors.online);
      expect(zoneStatusVisual(PlantConditionStatus.unknown).$1, AppColors.pending);
    });
  });

  group('metricAdviceText', () {
    test('names the direction and gives a suggestion, not just "bad"', () {
      final low = metricAdviceText('soil_moisture', PlantConditionStatus.low);
      expect(low, contains('drier'));
      expect(low, contains('watering'));

      final high = metricAdviceText('air_temperature', PlantConditionStatus.high);
      expect(high, contains('hotter'));
    });

    test('good and unknown are handled before the per-metric switch', () {
      expect(metricAdviceText('soil_moisture', PlantConditionStatus.good), contains('ideal'));
      expect(metricAdviceText('soil_moisture', PlantConditionStatus.unknown), contains('No reading'));
    });

    test('an unrecognised metric still says something directional, not garbage', () {
      expect(metricAdviceText('co2', PlantConditionStatus.low), contains('Below'));
      expect(metricAdviceText('co2', PlantConditionStatus.high), contains('Above'));
    });
  });

  group('historicalCompliance', () {
    test('averages the values and computes the % inside the range', () {
      const range = ClimateRange(min: 40, max: 60);
      final result = historicalCompliance([30, 50, 50, 70], range);
      expect(result.average, 50);
      expect(result.compliancePct, 50); // 2 of 4 values (50, 50) are in range
    });

    test('empty history is 0/0, not a division-by-zero crash', () {
      final result = historicalCompliance(const [], const ClimateRange(min: 40, max: 60));
      expect(result.average, 0);
      expect(result.compliancePct, 0);
    });

    test('all values inside the range is 100% compliance', () {
      final result = historicalCompliance([45, 50, 55], const ClimateRange(min: 40, max: 60));
      expect(result.compliancePct, 100);
    });
  });

  group('kMetricReadingsKey', () {
    test('covers exactly the three profile metrics, mapped to slash-separated keys', () {
      expect(kMetricReadingsKey['soil_moisture'], 'soil/moisture');
      expect(kMetricReadingsKey['air_temperature'], 'air/temperature');
      expect(kMetricReadingsKey['air_humidity'], 'air/humidity');
    });
  });
}
