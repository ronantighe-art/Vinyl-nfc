#include <M5StickCPlus.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <MFRC522_I2C.h>
#include <Wire.h>
#include "secrets.h"
// secrets.h defines WIFI_SSID, WIFI_PASSWORD, SERVER_IP — see secrets.h.example
// in the firmware folder. Copy that file to secrets.h and fill in your real
// values; secrets.h itself is gitignored and will never be pushed to GitHub.

const int SERVER_PORT = 5050;

// RFID2 unit's I2C address — confirmed via I2C scanner as 0x28.
MFRC522_I2C mfrc522(0x28, -1);

void connectWiFi() {
  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setCursor(0, 0);
  M5.Lcd.println("Connecting WiFi...");
  Serial.println("Connecting to WiFi...");

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
  } else {
    Serial.println("\nWiFi FAILED");
  }
}

void sendScan(const String& uid) {
  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setCursor(0, 0);

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Not connected to WiFi, skipping send");
    M5.Lcd.println("No WiFi!");
    return;
  }

  HTTPClient http;
  String url = "http://" + String(SERVER_IP) + ":" + String(SERVER_PORT) + "/scan";
  Serial.println("POSTing to: " + url);

  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  String body = "{\"uid\": \"" + uid + "\", \"room\": \"" + String(ROOM_NAME) + "\"}";
  int responseCode = http.POST(body);

  if (responseCode > 0) {
    String response = http.getString();
    Serial.println("Response code: " + String(responseCode));
    Serial.println("Response body: " + response);

    M5.Lcd.println("Tag: " + uid);
    M5.Lcd.println("Code: " + String(responseCode));
    if (responseCode == 200) {
      M5.Lcd.println("Playing!");
    } else if (responseCode == 404) {
      M5.Lcd.println("Not mapped yet");
    }
  } else {
    Serial.println("POST failed: " + http.errorToString(responseCode));
    M5.Lcd.println("POST FAILED");
  }

  http.end();
}

void setup() {
  M5.begin();
  M5.Lcd.setRotation(1);
  M5.Lcd.setTextSize(2);

  Serial.begin(115200);
  delay(500);

  Wire.begin(32, 33);
  mfrc522.PCD_Init();

  connectWiFi();
  delay(1000);

  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setCursor(0, 0);
  M5.Lcd.println("Tap a tag...");
  Serial.println("Ready. Tap a tag against the reader.");
}

void loop() {
  M5.update();

  if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
    String uid = "";
    for (byte i = 0; i < mfrc522.uid.size; i++) {
      if (mfrc522.uid.uidByte[i] < 0x10) uid += "0";
      uid += String(mfrc522.uid.uidByte[i], HEX);
    }
    uid.toUpperCase();

    Serial.println("Tag detected! UID: " + uid);
    sendScan(uid);

    delay(2000);  // avoid re-triggering on the same tap
    M5.Lcd.fillScreen(BLACK);
    M5.Lcd.setCursor(0, 0);
    M5.Lcd.println("Tap a tag...");
  }

  delay(50);
}

