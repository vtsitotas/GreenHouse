/// An inclusive [min, max] range for one metric. Either bound may be null,
/// meaning "no lower/upper limit" -- e.g. humidity profiles that only care
/// about a ceiling.
class ClimateRange {
  final double? min;
  final double? max;
  const ClimateRange({this.min, this.max});
}

/// A named set of ideal-condition ranges for a common greenhouse plant.
///
/// [ranges] keys are the *rule-engine* metric names (`soil_moisture`,
/// `air_temperature`, `air_humidity` -- see `models/weather_rule.dart` and
/// `rule_form_dialog.dart`'s `_zoneMetrics`), not the slash-separated keys
/// the live readings map uses (`soil/moisture`, ...). Using the rule
/// vocabulary here means a profile's ranges plug directly into a
/// [WeatherRule] suggestion with no translation step.
class PlantProfile {
  final String id;
  final String name;
  final Map<String, ClimateRange> ranges;
  const PlantProfile({required this.id, required this.name, required this.ranges});
}

/// One id reserved for "the user typed their own ranges" -- never appears in
/// [kBuiltInPlantProfiles].
const String kCustomPlantProfileId = 'custom';

/// General-purpose starting points, not agronomy authority -- each is
/// editable via the "Custom" option in the zone care sheet. Ranges chosen to
/// be broadly reasonable for a home/hobby greenhouse, deliberately a bit
/// generous rather than optimal-for-yield.
const List<PlantProfile> kBuiltInPlantProfiles = [
  PlantProfile(id: 'tomato', name: 'Tomato', ranges: {
    'soil_moisture': ClimateRange(min: 40, max: 70),
    'air_temperature': ClimateRange(min: 18, max: 27),
    'air_humidity': ClimateRange(min: 40, max: 70),
  }),
  PlantProfile(id: 'cucumber', name: 'Cucumber', ranges: {
    'soil_moisture': ClimateRange(min: 50, max: 80),
    'air_temperature': ClimateRange(min: 18, max: 28),
    'air_humidity': ClimateRange(min: 60, max: 80),
  }),
  PlantProfile(id: 'pepper', name: 'Pepper', ranges: {
    'soil_moisture': ClimateRange(min: 40, max: 65),
    'air_temperature': ClimateRange(min: 20, max: 28),
    'air_humidity': ClimateRange(min: 40, max: 60),
  }),
  PlantProfile(id: 'eggplant', name: 'Eggplant', ranges: {
    'soil_moisture': ClimateRange(min: 45, max: 70),
    'air_temperature': ClimateRange(min: 21, max: 29),
    'air_humidity': ClimateRange(min: 45, max: 65),
  }),
  PlantProfile(id: 'lettuce', name: 'Lettuce / leafy greens', ranges: {
    'soil_moisture': ClimateRange(min: 55, max: 80),
    'air_temperature': ClimateRange(min: 10, max: 22),
    'air_humidity': ClimateRange(min: 50, max: 70),
  }),
  PlantProfile(id: 'basil', name: 'Basil', ranges: {
    'soil_moisture': ClimateRange(min: 45, max: 70),
    'air_temperature': ClimateRange(min: 18, max: 30),
    'air_humidity': ClimateRange(min: 40, max: 60),
  }),
  PlantProfile(id: 'strawberry', name: 'Strawberry', ranges: {
    'soil_moisture': ClimateRange(min: 50, max: 75),
    'air_temperature': ClimateRange(min: 15, max: 26),
    'air_humidity': ClimateRange(min: 40, max: 70),
  }),
  PlantProfile(id: 'succulent', name: 'Succulent / cactus', ranges: {
    'soil_moisture': ClimateRange(min: 10, max: 30),
    'air_temperature': ClimateRange(min: 15, max: 32),
    'air_humidity': ClimateRange(max: 50),
  }),
];

PlantProfile? builtInPlantProfileById(String id) {
  for (final p in kBuiltInPlantProfiles) {
    if (p.id == id) return p;
  }
  return null;
}
