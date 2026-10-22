# PdM Agent: an AI assistant for predictive maintenance

PdM Agent watches machine sensors, spots trouble early, works out the likely cause, and
writes the work order for the maintenance team. Technicians can also ask it questions in
plain English, such as *"Why is pump P-101 running hot, and what do I do about it?"*, and get
answers taken from the plant's own data and manuals.

```
$ pdm-agent workflow

Alarm ALM-P-101-202510090920 | P-101 | bearing_temperature_c=86.3 (limit 85.0) | critical | 2025-10-09 09:20 | 360 min
  [+] diagnose             BRG-OVERHEAT (high), BRG-WEAR (medium), MISALIGN (medium)
  [+] check_history        1 open work order(s), none for this cause
  [+] check_spare_parts    SKF-6309-2Z x3, SKF-LGMT2-1KG x6, KSB-SEAL-65 x1 (REORDER)
  [+] draft_work_order     WO-AUTO-DB7874A7 corrective, priority urgent
  [~] notify_technician    message prepared, not sent
  [~] submit_work_order    kept in memory, not written to CMMS

Alarm ALM-P-101-202510301810 | P-101 | bearing_temperature_c=76.5 (limit 75.0) | warning | ...
  [+] diagnose             BRG-OVERHEAT (medium), BRG-WEAR (low), MISALIGN (low)
  [!] check_history        already covered by open WO-AUTO-DB7874A7
```

The demo runs on **6 months of real bearing temperature data** (26,000 readings, one every
10 minutes) from a cooling water pump.

## How it works

```
 sensors (CSV / OPC-UA / MQTT)          CMMS (work orders, assets, parts)        manuals
          |                                         |                               |
          v                                         v                               v
   +--------------+    +-------------+    +-------------------+    +----------------------+
   | 1. Monitor   | -> | 2. Diagnose | -> | 3. Act            |    | 4. Chat (Claude)     |
   | alarms +     |    | which known |    | check history and |    | answers questions    |
   | trend        |    | failure fits|    | parts, draft WO,  |    | using 1-3 + manuals  |
   | forecast     |    |             |    | notify, submit    |    | as tools             |
   +--------------+    +-------------+    +-------------------+    +----------------------+
```

1. **Monitor** (`sensors.py`). Raises an alarm when a reading goes over its limit, or jumps
   well above its own normal level for the last 24 hours. It also fits a trend line to
   predict how many days are left before the limit is reached.
2. **Diagnose** (`diagnosis.py`). Matches the alarm against the asset's known failure modes
   (bearing overheating, misalignment, ...) and ranks them with a confidence level. These are
   plain rules, so the same alarm always gets the same answer.
3. **Act** (`workflow.py`). Checks whether an open work order already covers the problem, so
   a sensor that keeps alarming doesn't flood the backlog. Then it checks spare parts stock,
   drafts a prioritised work order, notifies the technician and submits it.
4. **Chat** (`agent.py`). Claude handles the conversation and calls the tools above, plus a
   manual search, to answer from real data. Creating a work order always asks for your
   confirmation first.

**Sandbox mode is on by default.** Nothing is sent or saved until you add `--live`.

## Quick start

```bash
git clone https://github.com/NaqiyaHat/AI-Industrial-AGENT-.git
cd AI-Industrial-AGENT-
pip install -e ".[dev]"

pdm-agent scan                        # alarms + trend forecast from the sensor data
pdm-agent workflow                    # alarm -> diagnosis -> work order (sandbox)
pdm-agent status "cooling pump 1"     # everything known about one asset
pdm-agent search "bearing overheating"

export ANTHROPIC_API_KEY=sk-ant-...   # needed for chat only
pdm-agent chat                        # talk to the agent
pdm-agent chat "What should I check first on P-101?"
```

Everything except `chat` works offline with no API key.

## Use your own plant's data

All data lives in the `data/` folder:

| File | What it holds |
|------|---------------|
| `cmms/assets.json` | Your equipment: id, name, location, criticality (A/B/C), possible failure modes |
| `cmms/failure_modes.json` | Failure modes, the sensor readings that reveal them, and the fix |
| `cmms/spare_parts.json` | Spare parts, stock level and reorder point |
| `cmms/work_orders.json` | Existing work orders |
| `sensors/sensors.json` | Which CSV belongs to which asset, plus warning and critical limits |
| `sensors/*.csv` | Sensor readings as `timestamp,value` |
| `manuals/*.md` | Maintenance manuals, split into sections with `##` headings |

Point the tool at another folder with `pdm-agent --data /path/to/data scan`.

To connect a real CMMS (SAP PM, IBM Maximo, UpKeep...), replace the load and save methods
in `plant.py`. The workflow and agent don't need to change.

## Run the tests

```bash
pytest -q
```

The tests cover alarm detection, forecasting, diagnosis, the workflow (sandbox, live and
duplicate handling) and the Claude tool loop. The tool loop is tested with a fake client,
so the tests don't need an API key.

## Credits

The architecture is inspired by [Machina](https://github.com/LGDiMaggio/machina) by Luigi
Gianpio Di Maggio (Apache-2.0). Machina is a full framework for industrial maintenance agents
with connectors for SAP PM, Maximo, OPC-UA and MQTT. This project is a separate, smaller
implementation written from scratch, with no code copied.
