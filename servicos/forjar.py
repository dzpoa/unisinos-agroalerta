"""Teste de segurança: tenta enganar a plataforma e o ESP32 com mensagens forjadas.

Com a plataforma no ar (python iniciar.py), rode:  python -m servicos.forjar
Resultado esperado:
  - no log do ingestor (logs/ingestor.log): três linhas "REJEITADA (segurança)";
  - no Serial Monitor do Wokwi: "[risco] REJEITADO" para os dois alertas falsos,
    e o LED não muda.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from comum.assinatura import assinar
from comum.config import ESTACAO_ALVO, PREFIXO
from servicos.mqtt_util import conectar

CELULA = "87a976cc3ffffff"


def main() -> None:
    cliente = conectar("forjar")
    cliente.loop_start()
    agora = datetime.now(timezone.utc).isoformat()
    leitura = {"dispositivo_id": "teste-seguranca", "celula_h3": CELULA, "ts": agora, "fonte": "teste", "precip": 99.0}
    topico_leitura = f"{PREFIXO}/leituras/intruso/{CELULA}"
    topico_risco = f"{PREFIXO}/risco/{ESTACAO_ALVO}"

    testes = []
    # 1. Leitura sem assinatura (JSON puro, como era antes)
    testes.append(("leitura sem assinatura", topico_leitura, json.dumps(leitura)))
    # 2. Leitura assinada com a chave errada
    env = json.loads(assinar(leitura))
    env["hmac"] = "0" * 64
    testes.append(("leitura com HMAC falso", topico_leitura, json.dumps(env)))
    # 3. Leitura válida interceptada e reenviada (ataque de repetição): a 1ª é aceita, a 2ª não
    valida = assinar({**leitura, "precip": 0.0})
    testes.append(("leitura válida (aceita)", topico_leitura, valida))
    testes.append(("mesma leitura repetida", topico_leitura, valida))
    # 4. Alerta falso para o ESP32, sem assinatura
    falso = {"codigo": ESTACAO_ALVO, "nome": "Santa Maria", "probabilidade": 0.99, "alerta": True, "emitido_em": agora}
    testes.append(("alerta falso sem assinatura", topico_risco, json.dumps(falso)))
    # 5. Alerta falso com conteúdo alterado depois de assinado
    env = json.loads(assinar({**falso, "alerta": False, "probabilidade": 0.01}))
    env["carga"] = json.dumps(falso)
    testes.append(("alerta falso adulterado", topico_risco, json.dumps(env)))

    for nome, topico, payload in testes:
        print(f"[forjar] enviando: {nome}  ->  {topico}")
        cliente.publish(topico, payload, qos=1)  # sem retain: não substitui o risco retido do motor
        time.sleep(1)
    time.sleep(1)
    cliente.loop_stop()
    cliente.disconnect()
    print("[forjar] pronto. Confira logs/ingestor.log e o Serial Monitor do Wokwi.")


if __name__ == "__main__":
    main()
