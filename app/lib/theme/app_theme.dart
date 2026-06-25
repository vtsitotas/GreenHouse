import 'package:flutter/material.dart';
import 'app_colors.dart';

class AppTheme {
  static ThemeData light() => ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(seedColor: AppColors.brand, brightness: Brightness.light),
        cardTheme: const CardThemeData(color: AppColors.cardLight, elevation: 2, margin: EdgeInsets.all(8)),
        appBarTheme: const AppBarTheme(backgroundColor: AppColors.brand, foregroundColor: Colors.white, elevation: 0),
      );

  static ThemeData dark() => ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(seedColor: AppColors.brand, brightness: Brightness.dark),
        cardTheme: const CardThemeData(color: AppColors.cardDark, elevation: 2, margin: EdgeInsets.all(8)),
        appBarTheme: const AppBarTheme(backgroundColor: AppColors.brandDark, foregroundColor: Colors.white, elevation: 0),
      );
}
