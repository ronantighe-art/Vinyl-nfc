#include <M5StickCPlus2.h>

void setup() {
  auto cfg = M5.config();
  StickCP2.begin(cfg);
  StickCP2.Display.setRotation(1);
  StickCP2.Display.setTextSize(2);
  StickCP2.Display.println("Hello Ronan!");

  Serial.begin(115200);
  delay(500);
  Serial.println("StickC PLUS2 booted OK");
}

void loop() {
  Serial.println("still alive...");
  delay(2000);
}
