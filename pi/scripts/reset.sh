#!/bin/bash
# Resets the Pi to AP mode for WiFi reconfiguration.
sudo rm -f /etc/greenhouse/.wifi_configured
sudo reboot
