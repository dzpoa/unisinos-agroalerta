# AgroAlerta — rede colaborativa de alerta de chuva para produtores rurais

Projeto final da disciplina de **Internet das Coisas e Inteligência Artificial** (UNISINOS).

Propriedades rurais da **Fronteira Oeste e Campanha (RS)** compartilham leituras de suas estações
meteorológicas (pressão, temperatura, umidade, chuva e vento) via **MQTT**. Um modelo de **IA
(LightGBM)**, treinado com 10 anos de dados reais do **INMET**, usa as leituras da rede para prever
**chuva forte (≥ 5 mm) nas próximas 3 horas**. Quando uma tempestade entra pela fronteira, as
propriedades que estão na rota dela recebem o alerta a tempo de recolher o maquinário, suspender a
pulverização ou proteger a colheita: **um produtor avisa o outro**.

```mermaid
flowchart LR
    subgraph Borda["Borda (propriedades rurais)"]
        SIM["Simulador da rede<br/>9 estações (INMET)"]
        ESP["ESP32 no Wokwi<br/>DHT22 + pluviômetro"]
    end
    subgraph Nuvem["Plataforma"]
        HIVE[("HiveMQ<br/>(público)")]
        MQ[("Mosquitto")]
        ING["Ingestor<br/>lista branca LGPD"]
        DB[("SQLite /<br/>PostgreSQL")]
        MOT["Motor de alertas<br/>LightGBM"]
        DASH["Dashboard<br/>Streamlit"]
    end
    SIM -- "leituras (H3 + ID rotativo)" --> MQ
    ESP -- leituras --> HIVE <-- ponte --> MQ
    MQ --> ING --> DB --> MOT
    MOT -- "risco / alertas" --> MQ
    HIVE -- "risco → LED/buzzer" --> ESP
    DB --> DASH
```

## Componentes

| Pasta | O que faz |
|---|---|
| `ia/` | Leitura dos CSVs do INMET, engenharia de atributos, treino e avaliação dos modelos |
| `servicos/simulador.py` | Rede de estações: reproduz eventos reais do INMET ou uma tempestade sintética |
| `servicos/ingestor.py` | Assina o MQTT, valida e grava as leituras (descarta campos fora da lista branca) |
| `servicos/motor_alertas.py` | A cada hora da rede, calcula o risco por estação e publica alertas no MQTT |
| `dashboard/app.py` | Mapa ao vivo, séries por estação, ESP32, métricas do modelo e privacidade |
| `firmware/wokwi/` | Firmware do ESP32 (simulado no Wokwi): publica leituras e reage aos alertas |
| `comum/` | Configuração, banco, parser do INMET, atributos e pseudonimização (compartilhados) |
| `infra/` | Configuração do Mosquitto (com ponte para o HiveMQ) |

## Pré-requisitos

- **Python 3.11 ou mais novo**
- **Mosquitto** (broker MQTT) e a **biblioteca OpenMP** (usada pelo LightGBM):

| | macOS | Linux (Debian/Ubuntu) | Windows |
|---|---|---|---|
| Mosquitto | `brew install mosquitto` | `sudo apt install mosquitto` | instalador em [mosquitto.org/download](https://mosquitto.org/download/) |
| OpenMP | `brew install libomp` | `sudo apt install libgomp1` | já vem com o LightGBM (requer o Visual C++ Redistributable) |

> **Não deixe o Mosquitto rodando como serviço**: o `iniciar.py` sobe o broker com a configuração do
> projeto e precisa da porta 1883 livre. No Windows, o instalador cria o serviço *Mosquitto Broker*:
> pare-o em `services.msc` (ou `Stop-Service mosquitto` no PowerShell como administrador) e, se quiser,
> mude a inicialização para *Manual*.

O banco é **SQLite**, criado automaticamente em `dados/agroalerta.db` — nada a instalar. Para usar
PostgreSQL (ou TimescaleDB, recomendado em produção), basta definir `DATABASE_URL` no `.env`.

## 1. Instalação

**macOS / Linux**

```bash
git clone <url-do-repositorio>
cd unisinos-agroalerta
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
git clone <url-do-repositorio>
cd unisinos-agroalerta
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> Se o PowerShell bloquear o `Activate.ps1`, rode antes:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Opcional: copie `.env.example` para `.env` para mudar configurações (os padrões já funcionam).

## 2. Executar a demo

Os modelos já treinados com os dados reais estão em `modelos/`, então não é preciso baixar dados.
Em todos os comandos abaixo, o ambiente virtual deve estar ativado (o prompt começa com `(.venv)`).

**Terminal 1 — plataforma** (Mosquitto, ingestor, motor de alertas e dashboard):

```bash
python iniciar.py
```

O script confere os pré-requisitos, sobe os quatro serviços e os acompanha: se algum parar, ele
mostra o final do log e encerra tudo. **Ctrl+C** encerra a plataforma. Os logs ficam em `logs/`.

Abra o dashboard em **http://localhost:8501**.

**Terminal 2 — rede simulada:**

```bash
# Temporal real de 30/04/2024 (início das enchentes de maio no RS)
python -m servicos.simulador --modo replay --inicio "2024-04-29 09:00" --horas 42

# Outros eventos reais de 2024
python -m servicos.simulador --listar-eventos 10

# Tempestade sintética cruzando a região de oeste para leste
python -m servicos.simulador --modo sintetico
```

Opções úteis: `--segundos-por-hora 3` (velocidade da demo), `--ruido 0.3` (ruído de sensor barato),
`--aquecimento 24` (horas iniciais enviadas rapidamente para formar o histórico dos atributos).

## 3. Retreinar com os dados do INMET (opcional)

1. Baixe os arquivos anuais de **2015 a 2024** em **portal.inmet.gov.br/dadoshistoricos**
   (ou direto: `https://portal.inmet.gov.br/uploads/dadoshistoricos/<ano>.zip`).
2. Coloque os ZIPs (ou só os CSVs das 9 estações) em `dados/brutos/`. O script lê dentro dos ZIPs e
   ignora as demais estações.
3. Rode:

```bash
python -m ia.preparar_dados      # gera dados/processados/ e relatorios/cobertura_dados.csv
python -m ia.treinar             # gera modelos/, métricas e gráficos em relatorios/
```

| Papel | Estações |
|---|---|
| Sentinelas (fronteira) | Uruguaiana (A809), São Borja (A830), Quaraí (A831), Santana do Livramento (A804) |
| Intermediárias | Alegrete (A826), Santiago (A833), São Gabriel (A832), Bagé (A827) |
| Alvo principal | Santa Maria (A803) |

> Para testar o pipeline sem os dados reais: `python -m ia.gerar_dados_sinteticos` gera CSVs
> **sintéticos** no mesmo formato em `dados/brutos/sinteticos/`. Não use esses resultados no relatório.

## 4. ESP32 no Wokwi

1. Em [wokwi.com](https://wokwi.com), crie um projeto **ESP32** e substitua `sketch.ino` e `diagram.json`
   pelos arquivos de `firmware/wokwi/`; adicione as bibliotecas de `libraries.txt` (aba *Library Manager*).
2. Ajuste `PREFIXO` no firmware para o mesmo valor de `MQTT_PREFIXO` e de `infra/mosquitto.conf`.
3. Inicie a simulação. O ESP32 publica a cada 10 s no HiveMQ; a ponte do Mosquitto entrega ao sistema.
4. **Interação na demo:** ajuste temperatura/umidade no DHT22, gire o potenciômetro (pressão) e clique no
   botão azul (pluviômetro: 0,2 mm por pulso). Quando o motor emite alerta para Santa Maria, o **LED
   vermelho** acende e o **buzzer** toca.

> O Wokwi não tem sensor barométrico; o potenciômetro emula a saída de um BMP280/BME280.

## 5. Modelo de IA

- **Alvo:** chuva acumulada ≥ `LIMIAR_MM` (5 mm) nas próximas `HORIZONTE_H` (3) horas.
- **Atributos locais:** chuva acumulada (1/3/6/24 h), pressão e suas variações (1/3/6 h), temperatura,
  umidade, vento (componentes u/v), rajadas, hora do dia e mês (codificação cíclica).
- **Atributos da rede:** chuva, variação de pressão, umidade e rajadas das **estações vizinhas**, com
  defasagens de 1 a 6 h, e agregados (máximo, média e número de vizinhos com chuva).
- **Comparação:** persistência × LightGBM só local × LightGBM local + vizinhos. O ganho do último sobre
  o segundo mede o **valor da rede colaborativa**.
- **Divisão temporal** (sem contaminação (data leakage)): treino 2015–2022, validação 2023 (early
  stopping e limiar de decisão que maximiza o F1), teste 2024.
- **Métricas:** PR-AUC (principal, pois chuva forte é evento raro), F1, precisão, recall, ROC-AUC e Brier.
- Saídas em `relatorios/`: `metricas.csv`, `resumo.json`, `cobertura_dados.csv` e gráficos.

### Resultados (teste em 2024, dados reais do INMET)

| Modelo | PR-AUC médio (9 estações) | PR-AUC Santa Maria |
|---|---|---|
| Persistência | 0,326 | 0,401 |
| LightGBM — só local | 0,461 | 0,529 |
| LightGBM — local + vizinhos | **0,572** | **0,682** |

A rede melhora a previsão em **todas as 9 estações**, e o ganho cresce de oeste para leste: nas
sentinelas da fronteira (Uruguaiana, Quaraí, São Borja) é de +0,02 a +0,04; nas estações a leste
(São Gabriel, Santa Maria, Santiago, Livramento) é de +0,12 a +0,22. É o padrão esperado quando
as tempestades entram pelo oeste: quem está mais adiante na rota se beneficia mais dos avisos de quem
está antes. Em Santa Maria, os atributos mais importantes são a chuva recente nas estações vizinhas.

### O que já vem no repositório

- `modelos/`: modelos treinados com os dados reais (2015–2024) — a demo funciona sem baixar os dados.
- `dados/demo/horario_2024.parquet`: recorte de 2024 (dados públicos do INMET) para o modo replay.
  Ex.: o temporal de **30/04/2024**, início das enchentes de maio no RS:
  `python -m servicos.simulador --modo replay --inicio "2024-04-29 09:00" --horas 42`

## 6. Privacidade (LGPD)

- O dispositivo **nunca envia coordenadas**: envia a **célula H3** (resolução 7, ~5 km²).
- O identificador é um **UUID rotativo diário** (derivado de segredo + dispositivo + data).
- O ingestor aplica uma **lista branca de campos**; qualquer outro é descartado antes de gravar.
- Isto é **pseudonimização**, não anonimização (art. 12 da LGPD): em áreas rurais pouco povoadas, uma
  célula pode corresponder a uma única propriedade.

## Tópicos MQTT

| Tópico | Direção | Conteúdo |
|---|---|---|
| `<prefixo>/leituras/simulador/<celula>` | rede → plataforma | leitura horária da estação |
| `<prefixo>/leituras/wokwi/<celula>` | ESP32 → plataforma | leitura a cada 10 s |
| `<prefixo>/risco/<codigo>` (retido) | plataforma → dispositivos | probabilidade e estado de alerta |
| `<prefixo>/alertas/estacao/<codigo>` | plataforma → dispositivos | mensagem de alerta |

## Licença

MIT — ver `LICENSE`.
