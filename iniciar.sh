#!/usr/bin/env bash
# Sobe a plataforma AgroAlerta: broker MQTT (Mosquitto), ingestor, motor de alertas e dashboard.
# Ctrl+C encerra tudo. O simulador é executado à parte, em outro terminal.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="python3"
[ -x .venv/bin/python ] && PYTHON=".venv/bin/python"
[ -f .env ] && set -a && . ./.env && set +a

if ! command -v mosquitto >/dev/null 2>&1; then
  for caminho in /opt/homebrew/sbin /usr/local/sbin /usr/sbin; do
    [ -x "$caminho/mosquitto" ] && PATH="$caminho:$PATH" && break
  done
fi
command -v mosquitto >/dev/null 2>&1 || { echo "Mosquitto não encontrado. macOS: brew install mosquitto"; exit 1; }
[ -n "$(ls modelos/*.joblib 2>/dev/null)" ] || { echo "Nenhum modelo em modelos/. Rode: $PYTHON -m ia.treinar"; exit 1; }

mkdir -p logs
PIDS=()
encerrar() {
  echo -e "\nEncerrando..."
  kill ${PIDS[@]+"${PIDS[@]}"} 2>/dev/null || true
  wait 2>/dev/null || true
  exit 0
}
trap encerrar INT TERM

iniciar() {  # iniciar <nome> <comando...>
  local nome=$1; shift
  "$@" > "logs/$nome.log" 2>&1 &
  PIDS+=($!)
  echo "  $nome (pid $!) -> logs/$nome.log"
}

echo "Iniciando AgroAlerta:"
iniciar mosquitto mosquitto -c infra/mosquitto.conf
sleep 1
iniciar ingestor "$PYTHON" -m servicos.ingestor
iniciar motor "$PYTHON" -m servicos.motor_alertas
iniciar dashboard "$PYTHON" -m streamlit run dashboard/app.py --server.headless true
sleep 4

for i in "${!PIDS[@]}"; do
  if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
    echo "Falha ao iniciar um dos serviços; veja os arquivos em logs/."
    encerrar
  fi
done

cat <<EOF

Tudo no ar. Dashboard: http://localhost:8501
Em outro terminal, rode o simulador, por exemplo:
  $PYTHON -m servicos.simulador --modo replay --inicio "2024-04-29 09:00" --horas 42
  $PYTHON -m servicos.simulador --modo sintetico
Ctrl+C aqui encerra tudo.
EOF
wait
