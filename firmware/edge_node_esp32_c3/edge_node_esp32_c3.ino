#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <DHT.h>

// --- PIN DEFINITIONS ---
#define SOIL_DATA_PIN 2    // Analog pin for soil moisture
#define DHT_DATA_PIN 3     // Digital pin for DHT22
#define SOIL_PWR_PIN 4     // Power output for soil sensor
#define DHT_PWR_PIN 5      // Power output for DHT22

#define DHTTYPE DHT22
DHT dht(DHT_DATA_PIN, DHTTYPE);

// --- YOUR BRIDGE ESP32 MAC ADDRESS ---
uint8_t bridgeAddress[] = {0x20, 0x6E, 0xF1, 0x6C, 0xA1, 0xB0};
const char* ssid = "billredmi";

// ADDED SOIL MOISTURE TO STRUCT
typedef struct struct_message {
  float temperature;
  float humidity;
  int soil_moisture;
} struct_message;

struct_message myData;
esp_now_peer_info_t peerInfo;
int failCount = 0;

int32_t getWiFiChannel(const char *ssid_to_scan) {
  if (int32_t n = WiFi.scanNetworks()) {
    for (uint8_t i = 0; i < n; i++) {
      if (!strcmp(ssid_to_scan, WiFi.SSID(i).c_str())) {
        return WiFi.channel(i);
      }
    }
  }
  return 1;
}

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
  
  // 1. Setup power pins for sensors
  pinMode(SOIL_PWR_PIN, OUTPUT);
  pinMode(DHT_PWR_PIN, OUTPUT);
  // Keep them off by default
  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN, LOW);
  
  // 2. Setup Wi-Fi & ESP-NOW
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  Serial.println("Scanning for Wi-Fi channel...");
  int32_t channel = getWiFiChannel(ssid);
  
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  esp_now_register_send_cb(OnDataSent);

  memcpy(peerInfo.peer_addr, bridgeAddress, 6);
  peerInfo.channel = channel;
  peerInfo.encrypt = false;
  esp_now_add_peer(&peerInfo);
}

void loop() {
  // --- REAL DATA READING SEQUENCE ---
  
  // 1. Power up sensors
  digitalWrite(SOIL_PWR_PIN, HIGH);
  digitalWrite(DHT_PWR_PIN, HIGH);
  
  // 2. Wait for DHT22 to stabilize (needs 1.5 - 2 seconds)
  delay(2000);
  
  // 3. Read sensors
  dht.begin();
  myData.temperature = dht.readTemperature();
  myData.humidity    = dht.readHumidity();
  myData.soil_moisture = analogRead(SOIL_DATA_PIN);
  
  // 4. Power down sensors to save battery
  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN, LOW);

  // --- TRANSMISSION ---
  
  // Send the packet over the air
  esp_now_send(bridgeAddress, (uint8_t *)&myData, sizeof(myData));
  Serial.printf("Sent -> T: %.2f H: %.2f Soil: %d\n", myData.temperature, myData.humidity, myData.soil_moisture);
  
  delay(5000); // Wait 5 seconds before next loop

  // Self-healing check
  if (failCount >= 3) {
    Serial.println("Re-scanning channel...");
    int32_t channel = getWiFiChannel(ssid);
    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);
    failCount = 0;
  }
}
