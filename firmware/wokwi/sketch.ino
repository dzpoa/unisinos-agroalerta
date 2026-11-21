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
 *
 * Segurança: toda mensagem MQTT vai num envelope assinado com HMAC-SHA256
 *   {"carga": "<JSON como texto>", "nonce": "<aleatório>", "hmac": "<hex>"}
 * O HMAC cobre "nonce|carga". O ESP32 assina as leituras que envia e só aceita risco
 * com HMAC válido e emitido depois do último aceito (alertas falsos ou
 * repetidos no broker público são ignorados). Mesma chave de comum/config.py.
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <time.h>
#include "mbedtls/sha256.h"
#include "mbedtls/md.h"

// ---------- Configuração (mesmo prefixo do .env do projeto) ----------
const char* WIFI_SSID   = "Wokwi-GUEST";
const char* WIFI_SENHA  = "";
const char* MQTT_HOST   = "test.mosquitto.org";  // alternativa: broker.emqx.io
const int   MQTT_PORTA  = 1883;
const char* PREFIXO     = "unisinos-agroalerta";
const char* CELULA_H3   = "87a976cc3ffffff";   // propriedade próxima a Santa Maria (res. 7)
const char* ESTACAO_REF = "A803";              // recebe o risco calculado para Santa Maria
const char* SEGREDO     = "troque-este-segredo";
const char* CHAVE_HMAC  = "agroalerta-chave-demo";   // demo: em produção, gravada na instalação
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
String ultimoEmitido = "";

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

String hmacHex(const String& nonce, const char* carga, size_t tamanho) {
  unsigned char mac[32];
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, info, 1);
  mbedtls_md_hmac_starts(&ctx, (const unsigned char*)CHAVE_HMAC, strlen(CHAVE_HMAC));
  mbedtls_md_hmac_update(&ctx, (const unsigned char*)nonce.c_str(), nonce.length());
  mbedtls_md_hmac_update(&ctx, (const unsigned char*)"|", 1);
  mbedtls_md_hmac_update(&ctx, (const unsigned char*)carga, tamanho);
  mbedtls_md_hmac_finish(&ctx, mac);
  mbedtls_md_free(&ctx);
  char hex[65];
  for (int i = 0; i < 32; i++) sprintf(hex + 2 * i, "%02x", mac[i]);
  return String(hex);
}

// Comparação em tempo constante (não revela quantos caracteres batem).
bool iguais(const String& a, const char* b) {
  size_t n = a.length();
  if (strlen(b) != n) return false;
  unsigned char dif = 0;
  for (size_t i = 0; i < n; i++) dif |= a[i] ^ b[i];
  return dif == 0;
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
  JsonDocument env;
  const char *carga, *nonce, *mac;
  if (deserializeJson(env, payload, tamanho) || !(carga = env["carga"]) || !(nonce = env["nonce"]) || !(mac = env["hmac"])) {
    Serial.println("[risco] REJEITADO: mensagem sem assinatura");
    return;
  }
  if (!iguais(hmacHex(String(nonce), carga, strlen(carga)), mac)) {
    Serial.println("[risco] REJEITADO: HMAC inválido (alerta falso ou alterado)");
    return;
  }
  JsonDocument doc;
  if (deserializeJson(doc, carga)) return;
  // "emitido_em" é o relógio real do motor (o "ts" da demo é de 2024 e volta no tempo a cada replay).
  // ISO 8601 em UTC: ordem de texto = ordem no tempo. Igual é aceito (mensagem retida reenviada).
  String emitido = doc["emitido_em"] | "";
  if (emitido < ultimoEmitido) {
    Serial.println("[risco] REJEITADO: mais antigo que o último aceito (repetição)");
    return;
  }
  ultimoEmitido = emitido;
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
  mqtt.setBufferSize(1024);   // envelope assinado é maior que o JSON puro
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

  char carga[384];
  size_t len = serializeJson(doc, carga);
  String nonce = String(esp_random(), HEX) + String(esp_random(), HEX);

  JsonDocument env;
  env["carga"] = (const char*)carga;
  env["nonce"] = nonce;
  env["hmac"] = hmacHex(nonce, carga, len);
  char buf[768];
  size_t total = serializeJson(env, buf);
  mqtt.publish(topicoLeituras, buf, total);
  Serial.printf("[envio] %s\n", carga);
}
