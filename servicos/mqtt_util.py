"""Cliente MQTT compartilhado (paho-mqtt 2.x)."""
from __future__ import annotations

import time

import paho.mqtt.client as mqtt

from comum.config import MQTT_HOST, MQTT_PORTA


def conectar(nome: str, ao_conectar=None, ao_receber=None) -> mqtt.Client:
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{nome}-{int(time.time())}")
    if ao_conectar:
        cliente.on_connect = ao_conectar
    if ao_receber:
        cliente.on_message = ao_receber
    for tentativa in range(30):  # aguarda o broker subir (docker compose)
        try:
            cliente.connect(MQTT_HOST, MQTT_PORTA, keepalive=60)
            return cliente
        except OSError:
            print(f"[{nome}] broker {MQTT_HOST}:{MQTT_PORTA} indisponível, tentativa {tentativa + 1}/30")
            time.sleep(2)
    raise SystemExit(f"[{nome}] não foi possível conectar ao broker MQTT.")
