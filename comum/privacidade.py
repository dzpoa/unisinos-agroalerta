"""Pseudonimização na borda (LGPD): célula H3 no lugar da coordenada e ID rotativo diário.

Observação: isto é pseudonimização, não anonimização (art. 12 da LGPD). A célula H3
de uma propriedade rural pode, em áreas pouco povoadas, identificar o produtor.
Mitigações e riscos residuais estão discutidos no relatório.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import h3

from comum.config import RESOLUCAO_H3, SEGREDO_PSEUDONIMO

_NAMESPACE = uuid.UUID("6f1c2a52-8a1e-4c55-9a0e-6f8e3b2d9c10")

# Únicos campos aceitos em uma leitura; qualquer outro é descartado pelo ingestor.
CAMPOS_PERMITIDOS = {
    "dispositivo_id", "celula_h3", "ts", "fonte",
    "precip", "pressao", "temp", "umid", "vento_vel", "vento_dir", "rajada",
}


def celula(lat: float, lon: float, resolucao: int = RESOLUCAO_H3) -> str:
    """Converte a coordenada em célula H3 (a coordenada original não sai do dispositivo)."""
    return h3.latlng_to_cell(lat, lon, resolucao)


def id_rotativo(id_real: str, quando: datetime) -> str:
    """UUID que muda a cada dia: o mesmo dispositivo não é rastreável entre dias."""
    semente = f"{SEGREDO_PSEUDONIMO}:{id_real}:{quando:%Y-%m-%d}"
    return str(uuid.uuid5(_NAMESPACE, hashlib.sha256(semente.encode()).hexdigest()))


def filtrar_payload(payload: dict) -> dict:
    """Mantém apenas campos da lista branca (nenhum dado pessoal identificável)."""
    return {k: v for k, v in payload.items() if k in CAMPOS_PERMITIDOS}
