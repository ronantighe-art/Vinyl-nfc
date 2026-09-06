#include <M5StickCPlus2.h>
#include <MFRC522_I2C.h>
#include <Wire.h>

// RFID2 unit's I2C address — confirmed via I2C scanner as 0x28.
MFRC522_I2C mfrc522(0x28, -1);

void setup() {
  auto cfg = M5.config();
  StickCP2.begin(cfg);
  StickCP2.Display.setRotation(1);
  StickCP2.Display.setTextSize(2);

  Serial.begin(115200);
  delay(500);

  Wire.begin();
  mfrc522.PCD_Init();

  StickCP2.Display.println("Tap a tag...");
  Serial.println("RFID2 ready. Tap a tag against the reader.");
}

void loop() {
  StickCP2.update();

  if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
    String uid = "";
    for (byte i = 0; i < mfrc522.uid.size; i++) {
      if (mfrc522.uid.uidByte[i] < 0x10) uid += "0";
      uid += String(mfrc522.uid.uidByte[i], HEX);
    }
    uid.toUpperCase();

    Serial.println("Tag detected! UID: " + uid);

    StickCP2.Display.clear();
    StickCP2.Display.setCursor(0, 0);
    StickCP2.Display.println("Tag read:");
    StickCP2.Display.println(uid);

    delay(1500);  // brief pause so the same tap doesn't register twice
    StickCP2.Display.clear();
    StickCP2.Display.setCursor(0, 0);
    StickCP2.Display.println("Tap a tag...");
  }

  delay(50);
}
