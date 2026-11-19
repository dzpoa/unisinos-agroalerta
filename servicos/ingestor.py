"""Ingestor: assina <prefixo>/leituras/# no broker MQTT e grava as leituras no banco.

Validações: JSON bem-formado, campos obrigatórios presentes, lista branca de campos
(qualquer campo fora dela — p. ex. coordenadas ou nome — é descartado antes de gravar).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import h3
import pandas as pd

from comum import db
from comum.config import PREFIXO, VARIAVEIS
from comum.privacidade import filtrar_payload
from servicos.mqtt_util import conectar

OBRIGATORIOS = ("dispositivo_id", "celula_h3", "ts")
SQL = (f"INSERT INTO leituras (ts, recebido_em, dispositivo_id, celula_h3, fonte, {', '.join(VARIAVEIS)}) "
       f"VALUES ({', '.join('?' * (5 + len(VARIAVEIS)))})")
contagem = {"ok": 0, "rejeitadas": 0}


def ao_conectar(cliente, _userdata, _flags, codigo, _props):
    print(f"[ingestor] conectado (código {codigo}); assinando {PREFIXO}/leituras/#")
    cliente.subscribe(f"{PREFIXO}/leituras/#", qos=1)


def ao_receber(_cliente, _userdata, msg):
    try:
        bruto = json.loads(msg.payload)
        dados = filtrar_payload(bruto)
        if any(k not in dados for k in OBRIGATORIOS) or not h3.is_valid_cell(dados["celula_h3"]):
            raise ValueError("campos obrigatórios ausentes ou célula H3 inválida")
        ts = pd.Timestamp(dados["ts"])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        fonte = dados.get("fonte") or msg.topic.split("/")[-2]
        valores = [None if dados.get(v) is None else float(dados[v]) for v in VARIAVEIS]
        db.executar(SQL, (ts.isoformat(), datetime.now(timezone.utc).isoformat(), str(dados["dispositivo_id"]),
                          dados["celula_h3"], fonte, *valores))
        contagem["ok"] += 1
        descartados = set(bruto) - set(dados)
        if descartados:
            print(f"[ingestor] campos fora da lista branca descartados: {sorted(descartados)}")
        if contagem["ok"] % 50 == 0:
            print(f"[ingestor] {contagem['ok']} leituras gravadas, {contagem['rejeitadas']} rejeitadas")
    except Exception as erro:
        contagem["rejeitadas"] += 1
        print(f"[ingestor] mensagem rejeitada em {msg.topic}: {erro}")


def main() -> None:
    db.criar_esquema()
    cliente = conectar("ingestor", ao_conectar, ao_receber)
    cliente.loop_forever()


if __name__ == "__main__":
    main()
