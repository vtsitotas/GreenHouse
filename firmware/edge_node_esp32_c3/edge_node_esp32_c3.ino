#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h> // Required to manually set the Wi-Fi channel

// --- YOUR BRIDGE ESP32 MAC ADDRESS ---
uint8_t bridgeAddress[] = {0x20, 0x6E, 0xF1, 0x6C, 0xA1, 0xB0};

const char* ssid = "billredmi";

typedef struct struct_message {
  float temperature;
  float humidity;
} struct_message;

struct_message myData;
esp_now_peer_info_t peerInfo;
int failCount = 0;

// Helper to find your phone's Wi-Fi channel
int32_t getWiFiChannel(const char *ssid_to_scan) {
  if (int32_t n = WiFi.scanNetworks()) {
    for (uint8_t i = 0; i < n; i++) {
      if (!strcmp(ssid_to_scan, WiFi.SSID(i).c_str())) {
        return WiFi.channel(i);
      }
    }
  }
  return 1; // Default fallback if it can't find it
}

// --- UPDATED FOR ESP32 CORE V3.x ---
void OnDataSent(const wifi_tx_info_t *info, esp_now_send_status_t status) {
  Serial.print("Send Status: ");
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "OK" : "FAIL");
  
  if (status == ESP_NOW_SEND_SUCCESS) {
    failCount = 0;
  } else {
    failCount++;
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  // Find the exact Wi-Fi channel the Bridge is using
  Serial.println("Scanning for Wi-Fi channel...");
  int32_t channel = getWiFiChannel(ssid);
  Serial.printf("Found Wi-Fi '%s' on channel %d\n", ssid, channel);
  
  // Force the Edge Node to use that exact same channel for ESP-NOW
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  // Start ESP-NOW
  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  // Register the send callback
  esp_now_register_send_cb(OnDataSent);

  // Register the Bridge as an authorized peer
  memcpy(peerInfo.peer_addr, bridgeAddress, 6);
  peerInfo.channel = channel; // Make sure ESP-NOW peer uses the matched channel
  peerInfo.encrypt = false;

  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("Failed to add peer");
    return;
  }
}

void loop() {
  // Simulate some fake sensor readings
  myData.temperature = 22.0 + (random(0, 100) / 10.0);
  myData.humidity    = 40.0 + (random(0, 300) / 10.0);

  // Send the packet over the air
  esp_now_send(bridgeAddress, (uint8_t *)&myData, sizeof(myData));

  Serial.printf("Sent -> T: %.2f  H: %.2f\n", myData.temperature, myData.humidity);
  
  delay(5000); 

  // Self-healing: If we get 3 consecutive failures, re-scan the Wi-Fi channel
  if (failCount >= 3) {
    Serial.println("Too many failures, re-scanning channel...");
    int32_t channel = getWiFiChannel(ssid);
    Serial.printf("New channel: %d\n", channel);
    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);
    failCount = 0;
  }
}
