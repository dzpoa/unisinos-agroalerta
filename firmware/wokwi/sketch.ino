/*
 * AgroAlerta — estação meteorológica de uma propriedade rural (ESP32 simulado no Wokwi).
 *
 * Sensores (componentes do Wokwi):
 *   DHT22          -> temperatura e umidade do ar
 *   Potenciômetro  -> pressão atmosférica (o Wokwi não tem sensor barométrico; o potenciômetro
 *                     emula a saída de um BMP280/BME280 entre 990 e 1025 hPa)
 *   Botão          -> pluviômetro de báscula: cada pulso = 0,2 mm de chuva
 * Atuadores:
 *   LED vermelho + buzzer -> alerta de chuva forte recebido da rede
 *   LED verde             -> sem alerta
 *
 * Privacidade (LGPD): o dispositivo nunca envia coordenadas — apenas a célula H3 da
 * propriedade (configurada na instalação) e um ID que muda a cada dia (SHA-256 de
 * segredo + MAC + data).
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <time.h>
#include "mbedtls/sha256.h"

// ---------- Configuração (mesmo prefixo do .env do projeto) ----------
const char* WIFI_SSID   = "Wokwi-GUEST";
const char* WIFI_SENHA  = "";
const char* MQTT_HOST   = "test.mosquitto.org";  // alternativa: broker.emqx.io
const int   MQTT_PORTA  = 1883;
const char* PREFIXO     = "unisinos-agroalerta";
const char* CELULA_H3   = "87a976cc3ffffff";   // propriedade próxima a Santa Maria (res. 7)
const char* ESTACAO_REF = "A803";              // recebe o risco calculado para Santa Maria
const char* SEGREDO     = "troque-este-segredo";
const unsigned long INTERVALO_MS = 10000;

// ---------- Pinos ----------
const int PINO_DHT = 15, PINO_POT = 34, PINO_PLUV = 4;
const int PINO_LED_ALERTA = 2, PINO_LED_OK = 18, PINO_BUZZER = 19;

DHT dht(PINO_DHT, DHT22);
WiFiClient wifi;
PubSubClient mqtt(wifi);

volatile unsigned int pulsos = 0;
volatile unsigned long ultimoPulso = 0;
unsigned long ultimoEnvio = 0;
bool emAlerta = false;
char topicoLeituras[128], topicoRisco[96];

void IRAM_ATTR aoPulso() {                       // debounce de 200 ms
  unsigned long agora = millis();
  if (agora - ultimoPulso > 200) { pulsos++; ultimoPulso = agora; }
}

String idRotativo() {
  struct tm t; getLocalTime(&t);
  char dia[11]; strftime(dia, sizeof(dia), "%Y-%m-%d", &t);
  String semente = String(SEGREDO) + ":" + WiFi.macAddress() + ":" + dia;
  unsigned char hash[32];
  mbedtls_sha256((const unsigned char*)semente.c_str(), semente.length(), hash, 0);
  char hex[33];
  for (int i = 0; i < 16; i++) sprintf(hex + 2 * i, "%02x", hash[i]);
  return String(hex);
}

String agoraIso() {
  struct tm t; getLocalTime(&t);
  char buf[30]; strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S+00:00", &t);
  return String(buf);
}

void atualizarAtuadores() {
  digitalWrite(PINO_LED_ALERTA, emAlerta);
  digitalWrite(PINO_LED_OK, !emAlerta);
  if (emAlerta) tone(PINO_BUZZER, 880, 300); else noTone(PINO_BUZZER);
}

void aoReceber(char* topico, byte* payload, unsigned int tamanho) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, tamanho)) return;
  emAlerta = doc["alerta"] | false;
  float prob = doc["probabilidade"] | 0.0;
  Serial.printf("[risco] %s: %.0f%% -> %s\n", (const char*)(doc["nome"] | "?"), prob * 100,
                emAlerta ? "ALERTA: recolher maquinário / suspender pulverização" : "normal");
  atualizarAtuadores();
}

void conectarMqtt() {
  while (!mqtt.connected()) {
    // ID de conexão aleatório a cada tentativa: o broker não consegue ligar conexões ao dispositivo
    String id = "agroalerta-esp32-" + String(esp_random(), HEX);
    Serial.print("Conectando ao broker MQTT... ");
    if (mqtt.connect(id.c_str())) {
      Serial.println("ok");
      mqtt.subscribe(topicoRisco, 1);
    } else {
      Serial.printf("falhou (rc=%d), nova tentativa em 2 s\n", mqtt.state());
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PINO_PLUV, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PINO_PLUV), aoPulso, FALLING);
  pinMode(PINO_LED_ALERTA, OUTPUT); pinMode(PINO_LED_OK, OUTPUT); pinMode(PINO_BUZZER, OUTPUT);
  atualizarAtuadores();
  dht.begin();

  WiFi.begin(WIFI_SSID, WIFI_SENHA);
  Serial.print("Conectando ao Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) { delay(250); Serial.print("."); }
  Serial.println(" ok");
  configTime(0, 0, "pool.ntp.org");               // relógio em UTC
  struct tm t; while (!getLocalTime(&t)) delay(200);

  snprintf(topicoLeituras, sizeof(topicoLeituras), "%s/leituras/wokwi/%s", PREFIXO, CELULA_H3);
  snprintf(topicoRisco, sizeof(topicoRisco), "%s/risco/%s", PREFIXO, ESTACAO_REF);
  mqtt.setServer(MQTT_HOST, MQTT_PORTA);
  mqtt.setCallback(aoReceber);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(60);      // padrão da PubSubClient é 15 s; brokers públicos podem derrubar antes
  mqtt.setSocketTimeout(10);
}

void loop() {
  if (!mqtt.connected()) conectarMqtt();
  mqtt.loop();

  if (millis() - ultimoEnvio < INTERVALO_MS) return;
  ultimoEnvio = millis();

  float temp = dht.readTemperature(), umid = dht.readHumidity();
  float pressao = 990.0 + 35.0 * analogRead(PINO_POT) / 4095.0;
  noInterrupts(); unsigned int n = pulsos; pulsos = 0; interrupts();

  JsonDocument doc;
  doc["dispositivo_id"] = idRotativo();
  doc["celula_h3"] = CELULA_H3;
  doc["ts"] = agoraIso();
  doc["fonte"] = "wokwi";
  if (!isnan(temp)) doc["temp"] = temp;
  if (!isnan(umid)) doc["umid"] = umid;
  doc["pressao"] = pressao;
  doc["precip"] = n * 0.2;

  char buf[384];
  size_t len = serializeJson(doc, buf);
  mqtt.publish(topicoLeituras, buf, len);
  Serial.printf("[envio] %s\n", buf);
}
