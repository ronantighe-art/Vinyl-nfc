#include <M5StickCPlus2.h>
#include <Wire.h>

void setup() {
  auto cfg = M5.config();
  StickCP2.begin(cfg);
  StickCP2.Display.setRotation(1);
  StickCP2.Display.setTextSize(2);

  Serial.begin(115200);
  delay(500);

  Wire.begin();

  StickCP2.Display.println("Scanning I2C...");
  Serial.println("Scanning I2C bus...");

  int found = 0;
  for (byte addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    byte error = Wire.endTransmission();
    if (error == 0) {
      Serial.print("Device found at address 0x");
      if (addr < 16) Serial.print("0");
      Serial.println(addr, HEX);

      StickCP2.Display.println("Found: 0x" + String(addr, HEX));
      found++;
    }
  }

  if (found == 0) {
    Serial.println("No I2C devices found.");
    StickCP2.Display.println("None found");
  } else {
    Serial.println("Scan complete. " + String(found) + " device(s) found.");
  }
}

void loop() {
  delay(1000);
}
