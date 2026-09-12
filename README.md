# Nova — backend

FastAPI application. It contains:
- the MES domain: work orders, FEFO stock, TRS/OEE, downtime, quality, scheduling;
- the LangGraph agent and its 46 tools;
- the autonomous supervisor;
- the **Sparkplug B primary host**, which ingests PLC telemetry over MQTT and
  sends machine commands.

See the [root README](../README.md) for the full picture, configuration and API
reference, and [../simulator/README.md](../simulator/README.md) for the
simulated plant and the metric contract.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env              # set OPENAI_API_KEY, ADMIN_PHONE, AUTH_SECRET
python -m app.db.seed             # minimal master data
python -m scripts.migrate_mqtt    # once, for databases created before the MQTT rework
uvicorn app.main:app --reload --port 8000
```

The backend needs an MQTT broker (`MQTT_HOST`, default `localhost:1883`). It
starts without one and keeps reconnecting; in the meantime machine commands
fail with "broker MQTT non connecté".

## How machine data flows

```
PLC / nova-sim ──DBIRTH/DDATA──▶ broker ──▶ SparkplugHost (paho thread)
                                              │ queue
                                              ▼
                                   SparkplugIngestor (1 thread)
                                   seq/bdSeq tracking, aliases, registry
                                              │
                                   mapper: tags → MES events
                                              │
                                   event_service → state, TRS log, alerts
                                              │ after commit
                                   command ack released + WebSocket broadcast

Nova tool / supervisor / REST ──▶ machine_command_service ──DCMD──▶ broker ──▶ PLC
                                   (waits for Command/LastId ack, 5 s timeout)
```

Rules that keep this correct:

- **Telemetry is the only writer of machine state.** A command never changes
  state by itself: the machine's confirmed report does.
- **Never wait on a command inside an open write transaction.** SQLite has a
  single writer, and ingestion must be able to commit the confirmed state.
  Multi-step flows (launch with pre-emption, re-route, stop a line) live in
  `production_control_service` and commit between steps.
- **Counters are PLC totals.** The MES counts the delta against the last value,
  which is persisted in `sparkplug_device.last_values`, so production made
  while the MES was down is still counted.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

No broker or OpenAI key is needed. The command-loop tests inject a fake PLC
that answers DCMDs the way `nova-sim` does.
