#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <PubSubClient.h> // Install this via Library Manager

// --- Network Configuration ---
const char* ssid = "billredmi";
const char* password = "billtsit2003";
const char* mqtt_server = "10.70.155.202"; 
const int mqtt_port = 1884;
const char* mqtt_topic = "test/sensors/data";

WiFiClient espClient;
PubSubClient client(espClient);

typedef struct struct_message {
  float temperature;
  float humidity;
} struct_message;

struct_message myData;

void reconnectMQTT() {
  while (!client.connected()) {
    Serial.print("Attempting MQTT connection...");
    String clientId = "ESP32Bridge-";
    clientId += String(random(0xffff), HEX);
    
    if (client.connect(clientId.c_str())) {
      Serial.println("connected");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 5 seconds");
      delay(5000);
    }
  }
}

// --- UPDATED FOR ESP32 CORE V3.x ---
void OnDataRecv(const esp_now_recv_info_t *info, const uint8_t *incomingData, int len) {
  memcpy(&myData, incomingData, sizeof(myData));
  
  char macStr[18];
  snprintf(macStr, sizeof(macStr), "%02X:%02X:%02X:%02X:%02X:%02X",
           info->src_addr[0], info->src_addr[1], info->src_addr[2], 
           info->src_addr[3], info->src_addr[4], info->src_addr[5]);
           
  char jsonPayload[128];
  snprintf(jsonPayload, sizeof(jsonPayload), "{\"mac\":\"%s\",\"temperature\":%.2f,\"humidity\":%.2f}", 
           macStr, myData.temperature, myData.humidity);
           
  if (client.connected()) {
    client.publish(mqtt_topic, jsonPayload);
    Serial.printf("Forwarded to MQTT: %s\n", jsonPayload);
  } else {
    Serial.println("MQTT not connected, packet dropped.");
  }
}

void setup() {
  Serial.begin(115200);
  
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected.");

  client.setServer(mqtt_server, mqtt_port);

  if (esp_now_init() != ESP_OK) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }
  esp_now_register_recv_cb(OnDataRecv);
}

void loop() {
  if (!client.connected()) {
    reconnectMQTT();
  }
  client.loop(); 
}
