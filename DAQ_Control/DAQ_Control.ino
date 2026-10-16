#include <QNEthernet.h>
#include <SPI.h>
#include <SD.h>
#include <math.h>
#include <stdlib.h>

using namespace qindesign::network;

constexpr int   CS_PIN       = 36;      // chip-select pin for RTD2
constexpr float R_REF        = 4000.0f; // reference resistor (ohms)
constexpr float ALPHA        = 0.00385f;// RTD temp coefficient
constexpr float R_0          = 1000.0f; // RTD resistance at 0 °C
constexpr int   PRESSURE_PIN = 15;      // pressure sense pin
constexpr int   HEATER_PIN   = 32; 
constexpr int chipSelect = BUILTIN_SDCARD; // Chip-select pin for SD card


// Static IP for Teensy
IPAddress ip(192, 168, 1, 10);
IPAddress subnet(255, 255, 255, 0);
IPAddress gateway(0, 0, 0, 0);

// HTTP server on port 80
EthernetServer server(80);

// data vars
float temperature, pressure;
uint32_t lastSampleTime = 0;
const uint32_t SAMPLING_TIME = 1000; // 1 second
int v_out_p;
float i_out_p;
bool heater_stat;
bool sd_present = false;

// setpoints
float temp_setpoint = 80.0f;
float pressure_setpoint = 20.0f;

// data-logging file-name - change as required.
String sd_card_filename = "autoclave_run_temp_pressure_log.csv";
// millis() when the latest sample was taken
uint32_t sample_ms = 0;   

void setup() {

  while (!Serial && millis() < 3000) {
    delay(10);
  }

  // configure for temp sense
  SPI.begin();
  Serial.print("Starting measurement:");
  pinMode(CS_PIN, OUTPUT);
  digitalWrite(CS_PIN, HIGH);
  configure_MAX_module(); // configure MAX module settings.
  delay(100);

  // configure for pressure sense
  pinMode(PRESSURE_PIN, INPUT); 
  analogReadResolution(12);
  delay(100);

  // configure heater relay, keep heater off by default.
  pinMode(HEATER_PIN, OUTPUT);
  digitalWrite(HEATER_PIN, LOW);
  heater_stat = false;
  delay(100);

  // Initialize Ethernet with static IP
  Serial.begin(115200);
  Serial.println("Starting QNEthernet server");
  bool success = Ethernet.begin(ip, subnet, gateway);

  Serial.print("Initialization success: ");
  Serial.println(success);

  Serial.print("Teensy IP: ");
  Serial.println(Ethernet.localIP());

  server.begin();
  Serial.println("HTTP server started");
  delay(100);

  // check if SD card is present and can be initialized:
  if (SD.begin(chipSelect)) {
    Serial.println("SD Card initialized.");
    sd_present = true; // define this global variable to dictate behaviour later.
    // create .csv file and write header.
    File dataFile = SD.open(sd_card_filename.c_str(), FILE_WRITE); 
    if (dataFile) { // 
      dataFile.println("Time since program execution,Temperature(C),Pressure(Psi-g)");
      dataFile.close(); // Saves data safely to the card
    }
  }

}

void configure_MAX_module(){ // write initial settings configuration to MAX module.
  // Configuration register:
  // Bit 7 = 1: VBIAS ON
  // Bit 6 = 1: Auto-convert mode
  // Bit 5 = 0: 1-shot ADC conversion 
  // Bit 4 = 0: 2-wire / 3-wire selection
  // Bit 3-2 = 1: Fault detection cycle
  // Bit 1 = 0: Fault Clear status
  // Bit 0 = 0: 60Hz notch frequency for noise rejection filter.

  byte config = 0b11000000;
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE1));
  digitalWrite(CS_PIN, LOW);
  // 0x80 = write to register 0x00
  SPI.transfer(0x80);
  SPI.transfer(config); // write the configuration settings

  digitalWrite(CS_PIN, HIGH);
  SPI.endTransaction();
  
}

// function to read temperature from MAX31865 using SPI interface.
float read_temperature() {
  // declare starting of SPI transaction
  // settings include speedMaximum, dataOrder, and dataMode.
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE1));
  // select slave
  digitalWrite(CS_PIN, LOW);
  // select register address to read from. MAX31865 stores data in 0x01 and 0x02 registers.
  SPI.transfer(0x01);       // define register to read from first.

  // send 2 dummy bytes which generates clock cycle, returns 2 bytes of data.
  byte msb = SPI.transfer(0x00); 
  byte lsb = SPI.transfer(0x00);
  digitalWrite(CS_PIN, HIGH); 
  SPI.endTransaction(); // end communication.

  // concatent 2 bytes to get full integer value. Last bit is fault bit.
  uint16_t raw = ((uint16_t)msb << 8) | lsb;
  bool fault = raw & 0x01;  // extract bit 0
  if (fault){
    return 0; // 0 return will mean error.
    // build in functionality to check exact error from registers eventually.
  }
  uint16_t adc_code = raw >> 1; // discard fault bit.
  float R_RTD = (adc_code * R_REF) / 32768.0; // resistance of the RTD.
  float temp = (R_RTD-R_0) / (R_0 * ALPHA); // read temp, in degrees C
  return temp;
}

// function to read the current from the pressure sensor and convert to a pressure-reading.
// note on wire colors - blue is 2 (24V), white is 1 (v_out, neg supply).
float read_pressure(){
  int adc  = analogRead(PRESSURE_PIN); // 12 bits of resolution
  float voltage = adc * (3.3 / 4095.0); // voltage = adc * (ref voltage / max resolution)
  i_out_p = (voltage * 1000) / 150.0; // output current in mA
  pressure = -14.5 + ((i_out_p - 4.0) / 16.0) * 114.5 ;// pressure in psi-G
  return pressure;
}

// function to control heater.
bool control_temp(float temperature) {
  float threshold = 1.0; // tolerance range

  if (temperature < (temp_setpoint - threshold)) {
    // if temp less than (setpoint-threshold) turn heater on.
    digitalWrite(HEATER_PIN, HIGH);
    heater_stat = true;
  } else if (temperature > (temp_setpoint + threshold)) {
    // if temp more than (setpoint+threshold) turn heater off.
    digitalWrite(HEATER_PIN, LOW);
    heater_stat = false;
  } else {
    // when (set-thr) < temp < (set+temp), keep state unchanged (DO NOTHING).
  }

  return heater_stat;
}

void loop() {
  // sample data after every SAMPLING_TIME interval
  if (millis() - lastSampleTime >= SAMPLING_TIME) {
    lastSampleTime = millis();
    sample_ms = lastSampleTime;

    temperature = read_temperature();
    // Serial.print("temp: ");
    // Serial.println(temperature);
    pressure = read_pressure();
    // Serial.print("Pressure: ");
    //  Serial.println(pressure);
    heater_stat = control_temp(temperature);
    // If SD card is present, write to it after each read.
    if (sd_present){
      String data_string = String(millis()) + "," + String(temperature, 2) + "," + String(pressure, 2);
      File dataFile = SD.open(sd_card_filename.c_str(), FILE_WRITE); 
      if (dataFile) {
        dataFile.println(data_string);
        dataFile.close(); // Saves data safely to the card
      }
    }
  }
  // Check for HTTP clients
  EthernetClient client = server.accept();
  if (client) {
     handleHttpClient(client);
  }

}

// functions for communicating over ethernet.

// Extract a floating-point query parameter.
// For example: "GET /setpoints?temp=80.5&pressure=20.0 HTTP/1.1"
bool getFloatParameter(
    const String &request,
    const String &key,
    float &result
) {
  String searchKey = key + "=";
  int start = request.indexOf(searchKey);

  if (start < 0) return false;

  start += searchKey.length();

  int end = request.indexOf('&', start);
  if (end < 0) end = request.indexOf(' ', start);
  if (end < 0) return false;

  String value = request.substring(start, end);

  char *endPtr;
  float parsed = strtof(value.c_str(), &endPtr);

  if (endPtr == value.c_str() ||
      *endPtr != '\0' ||
      !isfinite(parsed)) {
    return false;
  }

  result = parsed;
  return true;
}

// Send an HTTP JSON response.
void sendJson(
    EthernetClient &client,
    int statusCode,
    const String &body
) {
  if (statusCode == 200) {
    client.println("HTTP/1.1 200 OK");
  } else {
    client.println("HTTP/1.1 400 Bad Request");
  }

  client.println("Content-Type: application/json");
  client.println("Connection: close");
  client.print("Content-Length: ");
  client.println(body.length());
  client.println();
  client.print(body);
}

void handleHttpClient(EthernetClient &client) {

  client.setTimeout(500);

  uint32_t startTime = millis();
  String requestLine = "";

  // Read the complete HTTP request headers.
  bool headersComplete = false;

  while (client.connected() &&
         millis() - startTime < 1000) {

    if (!client.available()) continue;

    String line = client.readStringUntil('\n');
    line.trim();

    if (requestLine.length() == 0) {
      requestLine = line;
    }

    if (line.length() == 0) {
      headersComplete = true;
      break;
    }
  }

  if (!headersComplete) {
    client.stop();
    return;
  }

  // -------------------------------------------
  // SETPOINT UPDATE
  // -------------------------------------------
  if (requestLine.startsWith("GET /setpoints?")) {

    float newTemp;
    float newPressure;

    bool validTemp = getFloatParameter(
      requestLine, "temp", newTemp
    );

    bool validPressure = getFloatParameter(
      requestLine, "pressure", newPressure
    );

    if (validTemp && validPressure) {

      // Store both setpoints only after validating both.
      temp_setpoint = newTemp;
      pressure_setpoint = newPressure;

      Serial.print("Temperature setpoint: ");
      Serial.println(temp_setpoint);

      Serial.print("Pressure setpoint: ");
      Serial.println(pressure_setpoint);

      sendJson(
        client,
        200,
        "{\"status\":\"ok\"}"
      );

    } else {
      sendJson(
        client,
        400,
        "{\"status\":\"invalid_setpoints\"}"
      );
    }

  }

  // -------------------------------------------
  // SENSOR MEASUREMENT REQUEST
  // -------------------------------------------
  else if (requestLine.startsWith("GET / ")) {

    String json = "{";
    json += "\"temperature\":" + String(temperature, 2);
    json += ",";
    json += "\"pressure\":" + String(pressure, 2);
    json += ",";
    json += "\"heater_stat\":" + String(heater_stat ? 1 : 0);   
    json += ",";
    json += "\"sample_ms\":" + String(sample_ms);
    json += "}";

    sendJson(client, 200, json);

  } else {

    sendJson(
      client,
      400,
      "{\"status\":\"unknown_route\"}"
    );

  }

  client.flush();
  client.stop();
}
