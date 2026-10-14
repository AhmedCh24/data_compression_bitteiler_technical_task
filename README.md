# How to run the FreeRTOS application

I developed and tested this on **Ubuntu 24.04**. FreeRTOS runs as a normal Linux process (POSIX port), so you don't need a board.

## What it does

A Python feeder replays the Intel Berkeley Lab data of mote 7 (`data/mote_7.csv`) and writes the latest temperature, humidity and light values to `data/temp.txt`, `data/humidity.txt` and `data/light.txt`.
The FreeRTOS node reads these files with scheduled tasks, compresses each sample to a small delta packet and sends it over UDP.
A local dashboard shows the raw and decoded values, the compression ratio, and the timing and health of the tasks.

## 1. Install the dependencies

```bash
sudo apt update
sudo apt install -y build-essential cmake git python3
```

The feeder and the dashboard only use the Python standard library, so you don't need pip for them.

## 2. Get the FreeRTOS kernel

From the project root (skip this if the `FreeRTOS-Kernel` folder is already there):

```bash
git clone https://github.com/FreeRTOS/FreeRTOS-Kernel.git
```

## 3. Build

```bash
cmake -S . -B build
cmake --build build -j
```

This gives you `build/sensor_monitor`.

## 4. Run

Open three terminals in the project root and start them in this order:

```bash
# terminal 1: dashboard
python3 scripts/simulator/dashboard_new.py

# terminal 2: FreeRTOS node
./build/sensor_monitor --speed 30

# terminal 3: sensor feeder
python3 scripts/simulator/sensor_simulator.py --speed 30
```

Then open http://127.0.0.1:8000 in your browser.

A few notes:

- Use the **same `--speed`** for the node and the feeder. With 30, one real second is 30 seconds of dataset time.
- `./build/sensor_monitor --duration 60` stops the node after 60 seconds and prints a summary. Otherwise stop it with Ctrl+C.
- If you prefer the terminal, `python3 scripts/simulator/dashboard.py` is a text version of the dashboard.
- The parts talk over UDP on localhost: port 5000 for compressed packets, 5001 for raw rows and 5002 for health reports.

## Data analysis scripts (optional)

The scripts in `scripts/data_preparation/` need a few extra packages:

```bash
python3 -m venv venv
source venv/bin/activate
pip install pandas numpy matplotlib zstandard
```
