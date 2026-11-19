"""Configuração central: estações, caminhos, parâmetros do alvo e variáveis de ambiente."""
from __future__ import annotations

import json
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_env(arquivo: Path) -> None:
    """Lê o .env (CHAVE=valor); variáveis já definidas no ambiente têm prioridade."""
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, _, valor = linha.partition("=")
            os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env(RAIZ / ".env")
DIR_BRUTOS = RAIZ / "dados" / "brutos"
DIR_PROCESSADOS = RAIZ / "dados" / "processados"
DIR_MODELOS = RAIZ / "modelos"
DIR_RELATORIOS = RAIZ / "relatorios"

ARQ_HORARIO = DIR_PROCESSADOS / "horario.parquet"
ARQ_ESTACOES = DIR_PROCESSADOS / "estacoes.json"
# Recorte de 2024 (dados públicos do INMET) versionado no repositório para a demo de replay.
ARQ_REPLAY_DEMO = RAIZ / "dados" / "demo" / "horario_2024.parquet"

# Estações automáticas do INMET — Fronteira Oeste e Campanha (RS).
# Coordenadas do cabeçalho dos CSVs do INMET (2024); após `ia.preparar_dados`,
# os valores do arquivo estacoes.json gerado têm prioridade (ver estacoes()).
ESTACOES_BASE = {
    "A809": {"nome": "Uruguaiana", "lat": -29.8400, "lon": -57.0819, "papel": "sentinela"},
    "A830": {"nome": "São Borja", "lat": -28.6500, "lon": -56.0164, "papel": "sentinela"},
    "A831": {"nome": "Quaraí", "lat": -30.3686, "lon": -56.4371, "papel": "sentinela"},
    "A804": {"nome": "Santana do Livramento", "lat": -30.7506, "lon": -55.4014, "papel": "sentinela"},
    "A826": {"nome": "Alegrete", "lat": -29.7092, "lon": -55.5256, "papel": "intermediaria"},
    "A833": {"nome": "Santiago", "lat": -29.1916, "lon": -54.8857, "papel": "intermediaria"},
    "A832": {"nome": "São Gabriel", "lat": -30.3414, "lon": -54.3108, "papel": "intermediaria"},
    "A827": {"nome": "Bagé", "lat": -31.3478, "lon": -54.0133, "papel": "intermediaria"},
    "A803": {"nome": "Santa Maria", "lat": -29.7250, "lon": -53.7206, "papel": "alvo"},
}
ESTACAO_ALVO = "A803"

# Alvo do modelo: chuva acumulada >= LIMIAR_MM nas próximas HORIZONTE_H horas.
LIMIAR_MM = float(os.getenv("LIMIAR_MM", "5.0"))
HORIZONTE_H = int(os.getenv("HORIZONTE_H", "3"))

# Divisão temporal (evita contaminação (data leakage)).
ANOS_TREINO = (2015, 2022)
ANO_VALIDACAO = 2023
ANO_TESTE = 2024

# Variáveis padronizadas usadas em todo o projeto.
VARIAVEIS = ["precip", "pressao", "temp", "umid", "vento_vel", "vento_dir", "rajada"]

# Infraestrutura.
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORTA = int(os.getenv("MQTT_PORTA", "1883"))
PREFIXO = os.getenv("MQTT_PREFIXO", "unisinos-agroalerta")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{RAIZ / 'dados' / 'agroalerta.db'}")
SEGREDO_PSEUDONIMO = os.getenv("SEGREDO_PSEUDONIMO", "troque-este-segredo")
RESOLUCAO_H3 = int(os.getenv("RESOLUCAO_H3", "7"))


def base_replay() -> Path:
    """Base horária completa, se preparada; senão, o recorte de 2024 versionado."""
    return ARQ_HORARIO if ARQ_HORARIO.exists() else ARQ_REPLAY_DEMO


def estacoes() -> dict:
    """Estações com coordenadas oficiais (se os dados já foram preparados)."""
    est = {k: dict(v) for k, v in ESTACOES_BASE.items()}
    if ARQ_ESTACOES.exists():
        oficiais = json.loads(ARQ_ESTACOES.read_text(encoding="utf-8"))
        for cod, meta in oficiais.items():
            if cod in est:
                est[cod].update({k: meta[k] for k in ("lat", "lon", "alt") if k in meta})
    return est
