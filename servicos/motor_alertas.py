"""Motor de alertas: a cada nova hora recebida da rede, calcula o risco de chuva forte
nas próximas horas para cada estação e avisa as propriedades na rota da tempestade.

Publica (mensagens assinadas com HMAC, ver comum/assinatura.py):
  <prefixo>/risco/<codigo>             estado atual (retido) — usado pelo ESP32 para o LED
  <prefixo>/alertas/estacao/<codigo>   somente quando o risco passa do limiar
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import joblib
import pandas as pd

from comum import db
from comum.assinatura import assinar
from comum.config import DIR_MODELOS, PREFIXO, VARIAVEIS, estacoes
from comum.features import montar_atributos, para_largo
from comum.privacidade import celula
from servicos.mqtt_util import conectar

CONFIG_MODELO = os.getenv("CONFIG_MODELO", "vizinhos")
INTERVALO_S = float(os.getenv("INTERVALO_MOTOR_S", "2"))
JANELA_H = 30


def carregar_modelos() -> dict:
    modelos = {}
    for arq in sorted(DIR_MODELOS.glob(f"modelo_*_{CONFIG_MODELO}.joblib")):
        pacote = joblib.load(arq)
        modelos[pacote["alvo"]] = pacote
    if not modelos:
        raise SystemExit(f"Nenhum modelo '{CONFIG_MODELO}' em {DIR_MODELOS}. Rode: python -m ia.treinar")
    return modelos


def janela(ts: pd.Timestamp, cel_para_cod: dict) -> pd.DataFrame:
    """Leituras da rede (simulador) nas últimas JANELA_H horas, no formato largo."""
    inicio = (ts - pd.Timedelta(hours=JANELA_H)).isoformat()
    df = db.consultar(f"SELECT ts, celula_h3, {', '.join(VARIAVEIS)} FROM leituras "
                      "WHERE fonte = 'simulador' AND ts >= ? AND ts <= ?", (inicio, ts.isoformat()))
    df["codigo"] = df["celula_h3"].map(cel_para_cod)
    df = df.dropna(subset=["codigo"]).drop(columns="celula_h3")
    largo = para_largo(df)
    todas = pd.MultiIndex.from_product([list(cel_para_cod.values()), VARIAVEIS])
    grade = pd.date_range(ts - pd.Timedelta(hours=JANELA_H), ts, freq="h")
    return largo.reindex(index=grade, columns=todas)


def processar(ts: pd.Timestamp, modelos: dict, est: dict, celulas: dict, cliente, publicar: bool = True) -> None:
    """Calcula o risco da hora `ts`; publica no MQTT só se `publicar` (evita rajadas no aquecimento)."""
    largo = janela(ts, {v: k for k, v in celulas.items()})
    agora = datetime.now(timezone.utc).isoformat()
    linhas, alertas = [], []
    for cod, pacote in modelos.items():
        x = montar_atributos(largo, cod, pacote["usar_vizinhos"]).reindex(columns=pacote["atributos"])
        prob = float(pacote["modelo"].predict_proba(x.iloc[[-1]])[:, 1][0])
        alerta = prob >= pacote["limiar"]
        linhas.append((ts.isoformat(), agora, cod, celulas[cod], prob, pacote["limiar"], alerta))
        estado = {"codigo": cod, "nome": est[cod]["nome"], "celula_h3": celulas[cod], "ts": ts.isoformat(),
                  "probabilidade": round(prob, 3), "alerta": bool(alerta), "emitido_em": agora,
                  "horizonte_h": pacote["horizonte_h"], "limiar_mm": pacote["limiar_mm"]}
        if publicar:
            cliente.publish(f"{PREFIXO}/risco/{cod}", assinar(estado), qos=1, retain=True)
        if alerta:
            estado["mensagem"] = (f"Risco de chuva forte (≥{pacote['limiar_mm']:g} mm) em até "
                                  f"{pacote['horizonte_h']}h na região de {est[cod]['nome']}: "
                                  "recolha o maquinário e suspenda a pulverização.")
            if publicar:
                cliente.publish(f"{PREFIXO}/alertas/estacao/{cod}", assinar(estado, ensure_ascii=False), qos=1)
            alertas.append(f"{est[cod]['nome']} ({prob:.0%})")
    db.executar_varios("INSERT INTO previsoes (ts, calculado_em, codigo, celula_h3, probabilidade, limiar, alerta) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)", linhas)
    sufixo = "" if publicar else "  (histórico: não publicado no MQTT)"
    print(f"[motor] {ts:%Y-%m-%d %H:%M} UTC  " + (f"ALERTA: {', '.join(alertas)}" if alertas else "sem alertas") + sufixo)


def main() -> None:
    db.criar_esquema()
    modelos = carregar_modelos()
    est = estacoes()
    celulas = {cod: celula(m["lat"], m["lon"]) for cod, m in est.items()}
    print(f"[motor] modelos carregados: {', '.join(modelos)} (config '{CONFIG_MODELO}')")
    cliente = conectar("motor-alertas")
    cliente.loop_start()

    ultimo_check = datetime.now(timezone.utc).isoformat()
    pendentes: set[pd.Timestamp] = set()
    while True:
        novos = db.consultar("SELECT ts, COUNT(DISTINCT celula_h3) AS n, MAX(recebido_em) AS ultimo FROM leituras "
                             "WHERE fonte = 'simulador' AND recebido_em > ? GROUP BY ts", (ultimo_check,))
        if not novos.empty:
            ultimo_check = pd.Timestamp(novos["ultimo"].max()).isoformat()
            pendentes |= set(novos["ts"])
        if pendentes:
            mais_recente = max(pendentes)
            completas = db.consultar("SELECT ts, COUNT(DISTINCT celula_h3) AS n FROM leituras "
                                     "WHERE fonte = 'simulador' AND ts = ? GROUP BY ts", (mais_recente.isoformat(),))
            pronto_recente = not completas.empty and completas["n"].iloc[0] >= len(celulas)
            # Uma hora é processada quando está completa ou quando já chegou uma hora posterior.
            prontas = [ts for ts in sorted(pendentes) if ts < mais_recente or pronto_recente]
            for ts in prontas:
                # Com várias horas acumuladas (aquecimento), só a mais recente vai para os dispositivos.
                processar(ts, modelos, est, celulas, cliente, publicar=(ts == prontas[-1]))
                pendentes.discard(ts)
        time.sleep(INTERVALO_S)


if __name__ == "__main__":
    main()
