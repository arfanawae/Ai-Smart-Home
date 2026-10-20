// NEXUS light — UNO Q microcontroller side (STM32U585).
//
// The Linux side (python/main.py) calls these over Arduino Bridge:
//   set_led("<0-100>")  0 = off (the last level is kept), 1-100 = on at that level
//                       -> "on,brightness" as actually applied, e.g. "1,73"
//   get_led()           -> "on,brightness"
// Both are registered with Bridge.provide_safe(): they run inside loop(), not on the RPC thread,
// which is required for anything that touches pins.
//
// M5: a common-anode RGB LED on D9 (later the MOSFET gate for the MR16 — also a PWM pin).

#include <Arduino_RouterBridge.h>

// Common-anode RGB LED, one colour channel on D9 (through a resistor). The common anode goes to
// 3.3 V — NOT 5 V: the MCU's pins are 3.3 V, so with the anode on 5 V a pin at 3.3 V ("off") still
// leaves ~1.7 V across the LED and it glows faintly at 0 %.
const int LIGHT_PIN = 9;
// Set true ONLY for a pin the UNO Q pinout lists as PWM. analogWrite on a pin without PWM can fault
// the MCU, which then stops answering the Bridge (light stuck in its last state until Stop/Run).
// While false, any level 1-100 turns the pin fully on; the level is still kept and reported.
const bool LIGHT_CAN_DIM = true;  // D9 is a PWM (~) pin on the UNO header — confirm on the UNO Q pinout
// Some boards wire the on-board LED so that LOW = lit. If the LED is ON when NEXUS says off (and
// OFF when it says on), set this to true.
const bool LIGHT_ACTIVE_LOW = true;  // common anode: the pin sinks current, LOW = lit

bool lightOn = false;
int brightness = 100;  // last level, kept while off (same model as NEXUS)

String reading() {
  return String(lightOn ? 1 : 0) + "," + String(brightness);
}

// Never mix the two output modes on one pin: on STM32 cores, once analogWrite has switched the pin
// to PWM, a later digitalWrite(LOW) may not reach it — the light then stays on while the sketch
// reports "off" (seen as: dim with the slider, then "ปิดไฟ" does nothing until Stop/Run).
// So a dimmable pin is driven by analogWrite for every level, including 0 and 100.
void applyOutput() {
  int level = lightOn ? brightness : 0;
  if (LIGHT_CAN_DIM) {
    int duty = map(level, 0, 100, 0, 255);
    analogWrite(LIGHT_PIN, LIGHT_ACTIVE_LOW ? 255 - duty : duty);
  } else {
    pinMode(LIGHT_PIN, OUTPUT);  // take the pin back from PWM if anything ever left it there
    bool lit = level > 0;
    digitalWrite(LIGHT_PIN, lit != LIGHT_ACTIVE_LOW ? HIGH : LOW);
  }
}

String set_led(String value) {
  int level = constrain(value.toInt(), 0, 100);  // last line of defence: never trust the caller
  lightOn = level > 0;
  if (lightOn) brightness = level;
  applyOutput();
  return reading();
}

String get_led() {
  return reading();
}

void setup() {
  pinMode(LIGHT_PIN, OUTPUT);
  applyOutput();  // start off: a reboot never switches the light on by itself
  Bridge.begin();
  Bridge.provide_safe("set_led", set_led);
  Bridge.provide_safe("get_led", get_led);
}

void loop() {
  // Bridge.provide_safe() callbacks are executed from here by the Bridge library.
}
