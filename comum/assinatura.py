"""Assinatura HMAC-SHA256 das mensagens MQTT (integridade e autenticidade).

Envelope publicado no broker:
    {"carga": "<JSON da mensagem, como texto>", "nonce": "<aleatório>", "hmac": "<hex>"}

O HMAC é calculado sobre "nonce|carga", exatamente como o texto foi enviado: assim o
ESP32 (ArduinoJson) e o Python não precisam formatar números da mesma forma.
O nonce torna cada envio único, o que permite rejeitar cópias de mensagens já recebidas
(proteção contra repetição).

Limitações (ver relatório):
- HMAC usa uma chave compartilhada: garante integridade e autenticidade, mas não
  não-repúdio. Em produção, o caminho seria ECDSA (chave privada só no dispositivo).
- Chave única da rede: uma chave por dispositivo exigiria um identificador fixo para
  escolher a chave, o que desfaria a pseudonimização do ID diário.
- Nesta demo a chave está no repositório e no projeto público do Wokwi; em produção
  ela ficaria só no .env e gravada no dispositivo na instalação.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections import OrderedDict

from comum.config import CHAVE_HMAC


class AssinaturaInvalida(ValueError):
    """Mensagem sem envelope, com HMAC incorreto ou repetida."""


def _calcular(nonce: str, carga: str) -> str:
    return hmac.new(CHAVE_HMAC.encode(), f"{nonce}|{carga}".encode(), hashlib.sha256).hexdigest()


def assinar(dados: dict, ensure_ascii: bool = True) -> str:
    """Devolve o envelope assinado, pronto para publicar."""
    carga = json.dumps(dados, ensure_ascii=ensure_ascii)
    nonce = secrets.token_hex(8)
    return json.dumps({"carga": carga, "nonce": nonce, "hmac": _calcular(nonce, carga)}, ensure_ascii=ensure_ascii)


def verificar(payload: bytes | str) -> tuple[dict, str]:
    """Confere o envelope e devolve (dados, hmac). Levanta AssinaturaInvalida se falhar."""
    try:
        env = json.loads(payload)
        carga, nonce, recebido = env["carga"], env["nonce"], env["hmac"]
    except (ValueError, KeyError, TypeError):
        raise AssinaturaInvalida("mensagem sem assinatura") from None
    if not isinstance(carga, str) or not hmac.compare_digest(_calcular(str(nonce), carga), str(recebido)):
        raise AssinaturaInvalida("HMAC inválido (chave errada ou conteúdo alterado)")
    return json.loads(carga), str(recebido)


class AntiRepeticao:
    """Memória das últimas N assinaturas aceitas; rejeita cópias de mensagens já recebidas."""

    def __init__(self, tamanho: int = 1000):
        self.tamanho = tamanho
        self._vistas: OrderedDict[str, None] = OrderedDict()

    def registrar(self, assinatura: str) -> None:
        if assinatura in self._vistas:
            raise AssinaturaInvalida("mensagem repetida (possível ataque de repetição)")
        self._vistas[assinatura] = None
        if len(self._vistas) > self.tamanho:
            self._vistas.popitem(last=False)
