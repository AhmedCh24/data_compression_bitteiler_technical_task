#!/usr/bin/env python3
"""Replay mote_7.csv against a dataset clock and expose rows to FreeRTOS.

The simulated clock starts at the first CSV date/time. At speed 1, dataset time
matches wall time; --speed 30 replays 30 seconds of dataset time per wall second.
Each due CSV row is written atomically to the three latest-sample files and also
sent as raw float32 values to the dashboard's raw-data UDP port.
"""
import argparse
import csv
import math
import socket
import struct
import time
from datetime import datetime, timedelta
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
CSV_DEFAULT = DATA_DIR / "mote_7.csv"
FILES = {
    "temperature": DATA_DIR / "temp.txt",
    "humidity": DATA_DIR / "humidity.txt",
    "light": DATA_DIR / "light.txt",
}
RAW_HOST = "127.0.0.1"
RAW_PORT = 5001
RAW_FORMAT = "!4sQqffff"
RAW_MAGIC = b"RAW1"

def parse_source_datetime(date_text, time_text, state):
    """Parse Intel Lab date + minute:second time values with hour rollovers.

    Values such as 05:16.6 and 05:46.5 advance by about 30 seconds. The
    minute field wraps at 59; infer elapsed hours within each calendar date.
    """
    base_date = datetime.strptime(date_text, "%d/%m/%Y")
    match = re.fullmatch(r"(\d{1,2}):(\d{1,2}(?:\.\d+)?)", time_text)
    if not match:
        return datetime.fromisoformat(f"{date_text}T{time_text}")
    minute = int(match.group(1))
    second = float(match.group(2))
    if minute > 59 or second >= 60:
        raise ValueError(f"invalid MM:SS.s timestamp '{time_text}'")
    previous = state.get("previous_time")
    if state.get("date") != date_text:
        state["date"] = date_text
        state["hour"] = 0
        previous = None
    if previous is not None and (minute, second) < previous:
        state["hour"] += 1
    state["previous_time"] = (minute, second)
    return base_date + timedelta(hours=state["hour"], minutes=minute, seconds=second)

def parse_rows(path):
    """Load rows and carry forward missing numeric values as unchanged.

    A blank/invalid sensor field reuses the most recent valid value for that
    field. Rows before all fields have an initial value are skipped because
    there is no previous measurement to carry forward.
    """
    rows = []
    skipped = []
    required = {"date", "time", "epoch", "moteid", "temperature", "humidity", "light", "voltage"}
    time_state = {}
    last_values = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"CSV must contain columns: {', '.join(sorted(required))}")
        for line_no, row in enumerate(reader, start=2):
            try:
                numeric = {}
                for key in ("temperature", "humidity", "light", "voltage"):
                    raw = row.get(key)
                    value = None
                    if raw is not None and raw.strip():
                        try:
                            parsed = float(raw)
                            if math.isfinite(parsed):
                                value = parsed
                        except ValueError:
                            pass
                    if value is None:
                        if key not in last_values:
                            raise ValueError(f"missing initial '{key}' value; nothing to carry forward")
                        value = last_values[key]
                    else:
                        last_values[key] = value
                    numeric[key] = value

                date_text = (row.get("date") or "").strip()
                time_text = (row.get("time") or "").strip()
                if not date_text or not time_text:
                    raise ValueError("missing date/time")
                dt = parse_source_datetime(date_text, time_text, time_state)
                rows.append({"dt": dt, "epoch": int(row["epoch"]),
                             "moteid": int(row["moteid"]), **numeric})
            except (TypeError, ValueError, KeyError) as exc:
                skipped.append((line_no, str(exc)))

    if not rows:
        detail = f"; first skipped rows: {skipped[:5]}" if skipped else ""
        raise ValueError(f"No usable data rows found in {path}{detail}")
    rows.sort(key=lambda item: item["dt"])
    if skipped:
        print(f"[FEEDER] skipped {len(skipped)} row(s) without enough initial values or valid metadata")
        for line_no, reason in skipped[:5]:
            print(f"[FEEDER] skipped CSV line {line_no}: {reason}")
        if len(skipped) > 5:
            print(f"[FEEDER] ... plus {len(skipped) - 5} more skipped row(s)")
    return rows

def write_latest(path, epoch, timestamp_ms, value):
    # Atomic replace prevents C from reading a partially written line.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(f"{epoch},{timestamp_ms},{value:.8f}\n", encoding="ascii")
    tmp.replace(path)

def main():
    parser = argparse.ArgumentParser(description="Replay mote_7.csv on its dataset timeline")
    parser.add_argument("--csv", type=Path, default=CSV_DEFAULT, help="CSV path (default: data/mote_7.csv)")
    parser.add_argument("--speed", type=float, default=30.0,
                        help="dataset-time speed multiplier; 1=real time, 30=30x faster (default: 30)")
    parser.add_argument("--loop", action="store_true", help="restart the replay after the last row")
    args = parser.parse_args()
    if args.speed <= 0:
        parser.error("--speed must be greater than zero")
    rows = parse_rows(args.csv)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in FILES.values():
        path.write_text("", encoding="ascii")

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    first_dt = rows[0]["dt"]
    wall_origin = time.monotonic()
    previous_epoch = None
    print(f"[FEEDER] source: {args.csv}")
    print(f"[FEEDER] mote: {rows[0]['moteid']}; rows: {len(rows)}")
    print(f"[FEEDER] dataset clock starts at {first_dt.isoformat(sep=' ')}")
    print(f"[FEEDER] replay speed: {args.speed:g}x; raw dashboard UDP: {RAW_HOST}:{RAW_PORT}")
    print("[FEEDER] waiting for each row's dataset timestamp before publishing it")
    try:
        while True:
            for row in rows:
                target_elapsed = (row["dt"] - first_dt).total_seconds() / args.speed
                while True:
                    remaining = target_elapsed - (time.monotonic() - wall_origin)
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 0.25))
                # Convert the source wall-clock timestamp for display; all channels share it.
                timestamp_ms = int(row["dt"].timestamp() * 1000)
                for key, filename in FILES.items():
                    write_latest(filename, row["epoch"], timestamp_ms, row[key])
                raw_packet = struct.pack(
                    RAW_FORMAT, RAW_MAGIC, row["epoch"], timestamp_ms,
                    row["temperature"], row["humidity"], row["light"], row["voltage"]
                )
                raw_sock.sendto(raw_packet, (RAW_HOST, RAW_PORT))
                gap = "" if previous_epoch is None or row["epoch"] == previous_epoch + 1 else " [source epoch gap]"
                print(f"[FEEDER epoch={row['epoch']} {row['dt'].strftime('%H:%M:%S.%f')}] "
                      f"T={row['temperature']:.4f} C H={row['humidity']:.4f}% "
                      f"L={row['light']:.2f} lux V={row['voltage']:.5f} V{gap}")
                previous_epoch = row["epoch"]
            if not args.loop:
                print("[FEEDER] end of CSV reached; waiting (Ctrl+C to stop)")
                while True:
                    time.sleep(1)
            # On loop, restart the simulated clock after the duration of the dataset.
            wall_origin = time.monotonic() + 0.25
    except KeyboardInterrupt:
        print("\n[FEEDER] stopped")
    finally:
        raw_sock.close()

if __name__ == "__main__":
    main()
