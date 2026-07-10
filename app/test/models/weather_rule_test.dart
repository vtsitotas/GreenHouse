import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/weather_rule.dart';

void main() {
  test('fromJson parses a weather-level rule with an action', () {
    final rule = WeatherRule.fromJson({
      'id': 'heat-fan', 'name': 'Heat wave ventilation', 'enabled': true,
      'trigger': {'metric': 'temperature', 'op': '>', 'value': 35},
      'action': {'actuator': 'fan1', 'command': 'ON'},
    });

    expect(rule.zone, isNull);
    expect(rule.metric, 'temperature');
    expect(rule.op, '>');
    expect(rule.value, 35.0);
    expect(rule.durationMinutes, isNull);
    expect(rule.actuatorId, 'fan1');
    expect(rule.command, 'ON');
    expect(rule.notify, true); // defaulted
  });

  test('fromJson parses a zone-prefixed metric and splits zone/metric apart', () {
    final rule = WeatherRule.fromJson({
      'id': 'zone1-dry', 'name': 'Zone 1 soil dry', 'enabled': true, 'notify': true,
      'trigger': {'metric': 'zone1/soil_moisture', 'op': '<', 'value': 15, 'duration_minutes': 2880},
    });

    expect(rule.zone, 'zone1');
    expect(rule.metric, 'soil_moisture');
    expect(rule.durationMinutes, 2880);
    expect(rule.actuatorId, isNull);
    expect(rule.command, isNull);
  });

  test('fromJson defaults notify to true when absent', () {
    final rule = WeatherRule.fromJson({
      'id': 'r1', 'name': 'R', 'enabled': true,
      'trigger': {'metric': 'temperature', 'op': '>', 'value': 1},
      'action': {'actuator': 'a', 'command': 'ON'},
    });
    expect(rule.notify, true);
  });

  test('fromJson respects notify: false', () {
    final rule = WeatherRule.fromJson({
      'id': 'r1', 'name': 'R', 'enabled': true, 'notify': false,
      'trigger': {'metric': 'temperature', 'op': '>', 'value': 1},
      'action': {'actuator': 'a', 'command': 'ON'},
    });
    expect(rule.notify, false);
  });

  test('toJson composes zone/metric back into a single wire-format string', () {
    const rule = WeatherRule(
      id: 'zone2-humid', name: 'Zone 2 too humid', enabled: true, notify: true,
      zone: 'zone2', metric: 'air_humidity', op: '>', value: 70.0,
      durationMinutes: 1440, actuatorId: null, command: null,
    );

    final json = rule.toJson();
    expect((json['trigger'] as Map)['metric'], 'zone2/air_humidity');
    expect((json['trigger'] as Map)['duration_minutes'], 1440);
    expect(json.containsKey('action'), false); // alert-only — no action key at all
  });

  test('toJson omits duration_minutes when null', () {
    const rule = WeatherRule(
      id: 'r1', name: 'R', enabled: true, notify: true,
      zone: null, metric: 'temperature', op: '>', value: 35.0,
      durationMinutes: null, actuatorId: 'fan1', command: 'ON',
    );

    final json = rule.toJson();
    expect((json['trigger'] as Map).containsKey('duration_minutes'), false);
    expect(json['action'], {'actuator': 'fan1', 'command': 'ON'});
  });

  test('round-trips through toJson/fromJson unchanged', () {
    const rule = WeatherRule(
      id: 'zone1-dry', name: 'Zone 1 soil dry', enabled: true, notify: false,
      zone: 'zone1', metric: 'soil_moisture', op: '<', value: 15.0,
      durationMinutes: 2880, actuatorId: null, command: null,
    );

    final roundTripped = WeatherRule.fromJson(rule.toJson());
    expect(roundTripped.toJson(), rule.toJson());
  });

  test('zoneLabel formats zone names, or "Weather" for null', () {
    const zoneRule = WeatherRule(
      id: 'r', name: 'n', enabled: true, notify: true,
      zone: 'zone3', metric: 'soil_moisture', op: '<', value: 1.0,
      durationMinutes: 60, actuatorId: null, command: null,
    );
    const weatherRule = WeatherRule(
      id: 'r', name: 'n', enabled: true, notify: true,
      zone: null, metric: 'temperature', op: '<', value: 1.0,
      durationMinutes: null, actuatorId: null, command: null,
    );
    expect(zoneRule.zoneLabel, 'Zone 3');
    expect(weatherRule.zoneLabel, 'Weather');
  });

  test('metricLabel describes zone metrics distinctly from weather metrics', () {
    const soilRule = WeatherRule(
      id: 'r', name: 'n', enabled: true, notify: true,
      zone: 'zone1', metric: 'soil_moisture', op: '<', value: 1.0,
      durationMinutes: 60, actuatorId: null, command: null,
    );
    expect(soilRule.metricLabel, 'Soil moisture (%)');
  });
}
