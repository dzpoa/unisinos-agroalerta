"""Simulador da rede colaborativa de estações rurais.

Cada estação do INMET representa a estação meteorológica de uma propriedade rural. O
simulador publica uma leitura por estação a cada "hora simulada", já pseudonimizada
(célula H3 + ID rotativo diário), no tópico <prefixo>/leituras/simulador/<celula_h3>.

Modos:
  replay     reproduz um período REAL da base do INMET (ex.: um temporal de 2024);
  sintetico  gera uma tempestade que cruza a região de oeste para leste (não exige dados).

Exemplos:
  python -m servicos.simulador --listar-eventos 10
  python -m servicos.simulador --modo replay --inicio "2024-05-01 00:00" --horas 48
  python -m servicos.simulador --modo sintetico --segundos-por-hora 3
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from comum.assinatura import assinar
from comum.config import (ANO_TESTE, ESTACAO_ALVO, base_replay, HORIZONTE_H, LIMIAR_MM,
                          PREFIXO, VARIAVEIS, estacoes)
from comum.features import para_largo
from comum.privacidade import celula, id_rotativo
from servicos.mqtt_util import conectar

KM_POR_GRAU = 111.0


def listar_eventos(n: int) -> None:
    """Maiores eventos de chuva na estação-alvo no ano de teste (bons para a demo)."""
    largo = para_largo(pd.read_parquet(base_replay()))
    p = largo[ESTACAO_ALVO]["precip"]
    acum = p.rolling(HORIZONTE_H).sum()
    acum = acum[acum.index.year == ANO_TESTE].dropna()
    eventos = []
    for ts, valor in acum.sort_values(ascending=False).items():
        if valor < LIMIAR_MM or len(eventos) >= n:
            break
        if all(abs((ts - e[0]).total_seconds()) > 48 * 3600 for e in eventos):
            eventos.append((ts, valor))
    print(f"Maiores eventos em {estacoes()[ESTACAO_ALVO]['nome']} ({ANO_TESTE}):")
    for ts, valor in eventos:
        inicio = (ts - pd.Timedelta(hours=30)).strftime("%Y-%m-%d %H:%M")
        print(f"  {ts:%Y-%m-%d %H:%M} UTC  {valor:5.1f} mm/{HORIZONTE_H}h  ->  --inicio \"{inicio}\" --horas 42")


def tempestade_demo(horas: int, semente: int = 7) -> pd.DataFrame:
    """Tempestade sintética que entra pela fronteira oeste e segue para leste-nordeste."""
    rng = np.random.default_rng(semente)
    est = estacoes()
    agora = pd.Timestamp.now(tz="UTC").floor("h")
    tempos = pd.date_range(agora - pd.Timedelta(hours=horas - 1), agora, freq="h")
    pico = horas - 20  # a célula passa pela fronteira cerca de 20h antes do fim
    linhas = []
    for cod, meta in est.items():
        alt = meta.get("alt", 150) or 150
        for k, ts in enumerate(tempos):
            horas_tempestade = k - (pico - 10)
            clon = -58.3 + 0.42 * horas_tempestade  # ~45 km/h para leste
            clat = -30.3 + 0.08 * horas_tempestade
            dx = (meta["lon"] - clon) * KM_POR_GRAU * np.cos(np.deg2rad(meta["lat"]))
            d = np.hypot(dx, (meta["lat"] - clat) * KM_POR_GRAU)
            nucleo = np.exp(-(d / 70) ** 2) if horas_tempestade >= 0 else 0.0
            frente = np.exp(-((d - 110) / 70) ** 2) * (dx > 0) if horas_tempestade >= 0 else 0.0
            hora = 2 * np.pi * ts.hour / 24
            linhas.append({
                "ts": ts, "codigo": cod,
                "precip": round(float(14 * nucleo if 14 * nucleo > 0.2 else 0.0), 1),
                "pressao": round(1013 - alt * 0.12 + np.sin(2 * hora) - 4 * (nucleo + frente)
                                 + rng.normal(0, 0.2), 1),
                "temp": round(24 + 5 * np.sin(hora - 2) - 6 * nucleo + rng.normal(0, 0.5), 1),
                "umid": float(np.clip(65 - 12 * np.sin(hora - 2) + 30 * (nucleo + frente) + rng.normal(0, 3), 10, 100)),
                "vento_vel": round(abs(rng.normal(3, 1)) + 6 * nucleo, 1),
                "vento_dir": float(rng.uniform(250, 300)),  # vento de oeste
                "rajada": round(abs(rng.normal(6, 1.5)) + 18 * nucleo, 1),
            })
    return pd.DataFrame(linhas)


def publicar(dados: pd.DataFrame, segundos_por_hora: float, aquecimento: int, ruido: float) -> None:
    est = estacoes()
    celulas = {cod: celula(m["lat"], m["lon"]) for cod, m in est.items()}
    cliente = conectar("simulador")
    cliente.loop_start()
    rng = np.random.default_rng()
    horas = sorted(dados["ts"].unique())
    print(f"Publicando {len(horas)} horas simuladas ({aquecimento} de aquecimento) em {PREFIXO}/leituras/simulador/...")
    for i, ts in enumerate(horas):
        ts = pd.Timestamp(ts)
        for _, r in dados[dados["ts"] == ts].iterrows():
            if r["codigo"] not in celulas:
                continue
            leitura = {v: (None if pd.isna(r[v]) else round(float(r[v]) + (rng.normal(0, ruido) if ruido and v in ("pressao", "temp") else 0), 2))
                       for v in VARIAVEIS}
            payload = {"dispositivo_id": id_rotativo(r["codigo"], ts), "celula_h3": celulas[r["codigo"]],
                       "ts": ts.isoformat(), "fonte": "simulador", **leitura}
            cliente.publish(f"{PREFIXO}/leituras/simulador/{payload['celula_h3']}", assinar(payload), qos=1)
        chuva = dados[(dados["ts"] == ts) & (dados["precip"] > 0)]
        resumo = ", ".join(f"{est[c]['nome']} {p:.1f}mm" for c, p in zip(chuva["codigo"], chuva["precip"]) if c in est)
        print(f"  {ts:%Y-%m-%d %H:%M} UTC  {'chuva: ' + resumo if resumo else 'sem chuva'}")
        time.sleep(0.05 if i < aquecimento else segundos_por_hora)
    time.sleep(1)
    cliente.loop_stop()
    cliente.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modo", choices=["replay", "sintetico"], default="sintetico")
    ap.add_argument("--inicio", help="replay: início em UTC, ex. '2024-05-01 00:00'")
    ap.add_argument("--horas", type=int, default=48)
    ap.add_argument("--segundos-por-hora", type=float, default=3.0, help="1 hora simulada = N segundos reais")
    ap.add_argument("--aquecimento", type=int, default=24, help="horas iniciais publicadas rapidamente (histórico p/ os atributos)")
    ap.add_argument("--ruido", type=float, default=0.0, help="desvio-padrão de ruído de sensor barato (pressão/temperatura)")
    ap.add_argument("--listar-eventos", type=int, metavar="N")
    args = ap.parse_args()

    if args.listar_eventos:
        return listar_eventos(args.listar_eventos)
    if args.modo == "replay":
        if not args.inicio:
            raise SystemExit("Informe --inicio (use --listar-eventos para sugestões).")
        longo = pd.read_parquet(base_replay())
        inicio = pd.Timestamp(args.inicio, tz="UTC")
        dados = longo[(longo["ts"] >= inicio) & (longo["ts"] < inicio + pd.Timedelta(hours=args.horas))]
    else:
        dados = tempestade_demo(args.horas)
    publicar(dados, args.segundos_por_hora, args.aquecimento, args.ruido)


if __name__ == "__main__":
    main()
