"""Sobe a plataforma AgroAlerta (macOS, Linux e Windows).

Inicia o broker MQTT (Mosquitto), o ingestor, o motor de alertas e o dashboard, e
acompanha os processos: se algum cair, mostra o final do log dele e encerra tudo.
Ctrl+C encerra tudo. O simulador é executado à parte, em outro terminal.

Uso (com o ambiente virtual ativado):  python iniciar.py
"""
from __future__ import annotations

import importlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
LOGS = RAIZ / "logs"
SISTEMA = platform.system()  # "Darwin", "Linux" ou "Windows"

LOCAIS_MOSQUITTO = [
    "/opt/homebrew/sbin/mosquitto", "/usr/local/sbin/mosquitto", "/usr/sbin/mosquitto",
    r"C:\Program Files\mosquitto\mosquitto.exe", r"C:\Program Files (x86)\mosquitto\mosquitto.exe",
]
INSTALAR_MOSQUITTO = {
    "Darwin": "brew install mosquitto",
    "Linux": "sudo apt install mosquitto",
    "Windows": "instalador em https://mosquitto.org/download/",
}
INSTALAR_OPENMP = {
    "Darwin": "brew install libomp",
    "Linux": "sudo apt install libgomp1",
    "Windows": "instale o Microsoft Visual C++ Redistributable (x64) mais recente",
}


def checar_ambiente() -> str:
    """Valida pré-requisitos e retorna o caminho do Mosquitto."""
    if sys.version_info < (3, 11):
        sair(f"Python 3.11+ é necessário (atual: {platform.python_version()}).")
    if sys.prefix == sys.base_prefix:
        print("Aviso: o ambiente virtual (.venv) não parece ativado.\n")
    try:
        for modulo in ("lightgbm", "paho.mqtt", "streamlit", "h3", "pandas"):
            importlib.import_module(modulo)  # lightgbm falha aqui quando falta a biblioteca OpenMP
    except ModuleNotFoundError as erro:
        sair(f"Dependência ausente ({erro.name}). Rode: pip install -r requirements.txt")
    except OSError as erro:
        sair(f"O LightGBM não carregou ({erro}).\nInstale a biblioteca OpenMP: {INSTALAR_OPENMP.get(SISTEMA, '')}")

    if not list((RAIZ / "modelos").glob("*.joblib")):
        sair("Nenhum modelo em modelos/. Rode: python -m ia.treinar")

    mosquitto = shutil.which("mosquitto") or next((c for c in LOCAIS_MOSQUITTO if Path(c).exists()), None)
    if not mosquitto:
        sair(f"Mosquitto não encontrado. Instale: {INSTALAR_MOSQUITTO.get(SISTEMA, 'mosquitto.org/download')}")

    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 1883)) == 0:
            dica = ("No Windows, pare o serviço 'Mosquitto Broker': services.msc, ou no PowerShell como "
                    "administrador: Stop-Service mosquitto" if SISTEMA == "Windows"
                    else "Se for um Mosquitto rodando como serviço: brew services stop mosquitto "
                         "(macOS) ou sudo systemctl stop mosquitto (Linux)")
            sair(f"A porta 1883 já está em uso (outro broker MQTT ou execução anterior).\n{dica}")
    return mosquitto


def sair(mensagem: str) -> None:
    print(f"\nERRO: {mensagem}")
    sys.exit(1)


def ultimas_linhas(caminho: Path, n: int = 15) -> str:
    try:
        return "\n".join(caminho.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return "(log vazio)"


def main() -> None:
    os.chdir(RAIZ)
    mosquitto = checar_ambiente()
    LOGS.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}

    servicos = {
        "mosquitto": [mosquitto, "-c", str(RAIZ / "infra" / "mosquitto.conf")],
        "ingestor": [sys.executable, "-m", "servicos.ingestor"],
        "motor": [sys.executable, "-m", "servicos.motor_alertas"],
        "dashboard": [sys.executable, "-m", "streamlit", "run", "dashboard/app.py", "--server.headless", "true"],
    }
    processos: dict[str, tuple[subprocess.Popen, Path]] = {}

    def encerrar(codigo: int = 0) -> None:
        print("\nEncerrando...")
        for proc, _ in processos.values():
            if proc.poll() is None:
                proc.terminate()
        for proc, _ in processos.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(codigo)

    print("Iniciando AgroAlerta:")
    try:
        for nome, comando in servicos.items():
            log = LOGS / f"{nome}.log"
            arquivo = open(log, "w", encoding="utf-8")
            processos[nome] = (subprocess.Popen(comando, stdout=arquivo, stderr=subprocess.STDOUT, env=env), log)
            print(f"  {nome:<10} -> logs/{log.name}")
            time.sleep(1.5 if nome == "mosquitto" else 0.2)

        python = "python" if SISTEMA == "Windows" else "python3"
        print(f"""
Tudo no ar. Dashboard: http://localhost:8501
Em outro terminal (com o ambiente virtual ativado), rode o simulador, por exemplo:
  {python} -m servicos.simulador --modo replay --inicio "2024-04-29 09:00" --horas 42
  {python} -m servicos.simulador --modo sintetico
Ctrl+C aqui encerra tudo.""")

        while True:  # vigia os serviços
            for nome, (proc, log) in processos.items():
                if proc.poll() is not None:
                    print(f"\nO serviço '{nome}' parou (código {proc.returncode}). Final de logs/{log.name}:\n")
                    print(ultimas_linhas(log))
                    encerrar(1)
            time.sleep(2)
    except KeyboardInterrupt:
        encerrar(0)


if __name__ == "__main__":
    main()
