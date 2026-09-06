#include <M5StickCPlus2.h>
#include <WiFi.h>
#include "secrets.h"
// secrets.h defines WIFI_SSID, WIFI_PASSWORD — see secrets.h.example

void setup() {
  auto cfg = M5.config();
  StickCP2.begin(cfg);
  StickCP2.Display.setRotation(1);
  StickCP2.Display.setTextSize(2);

  Serial.begin(115200);
  delay(500);

  StickCP2.Display.println("Connecting to WiFi...");
  Serial.println("Connecting to WiFi...");

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  StickCP2.Display.clear();
  StickCP2.Display.setCursor(0, 0);

  if (WiFi.status() == WL_CONNECTED) {
    String ip = WiFi.localIP().toString();
    Serial.println("");
    Serial.println("WiFi connected!");
    Serial.println("IP address: " + ip);

    StickCP2.Display.println("WiFi connected!");
    StickCP2.Display.println(ip);
  } else {
    Serial.println("");
    Serial.println("WiFi connection FAILED after 15 seconds");
    StickCP2.Display.println("WiFi FAILED");
    StickCP2.Display.println("Check SSID/password");
  }
}

void loop() {
  // Nothing here yet — this sketch only proves the connection happens once.
  delay(1000);
}
