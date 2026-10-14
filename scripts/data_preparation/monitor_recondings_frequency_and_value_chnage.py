#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CSV_DIR = ROOT / "data/data_by_sensor_node/csv"        # folder with your 54 csv files
print(CSV_DIR)
LOG_DIR = ROOT / "data/data_by_sensor_node/ogs"        # logs are written here


def to_seconds(date, hour):
    return datetime.strptime(f"{date} {hour}", "%Y-%m-%d %H:%M:%S.%f").timestamp()


def analyse(name, values):
    diffs = []
    abs_diffs = []
    for i in range(len(values) - 1):
        d = values[i + 1] - values[i]
        diffs.append(d)
        abs_diffs.append(abs(d))

    text = f"\n=== {name} ===\n"
    text += f"Average difference (n+1 - n): {sum(diffs) / len(diffs):.4f}\n"
    text += f"Average absolute difference : {sum(abs_diffs) / len(abs_diffs):.4f}\n"
    text += f"Biggest jump                : {max(abs_diffs):.4f}\n"
    return text


def analyse_file(csv_file):
    lines = csv_file.read_text().splitlines()[1:]   # skip the header

    times = []
    temps = []
    hums = []
    lights = []

    for line in lines:
        try:
            date, hour, epoch, moteid, temp, hum, light, volt = line.split(",")
            t = to_seconds(date, hour)
            temp = float(temp)
            hum = float(hum)
            light = float(light)
        except ValueError:
            continue   # skip empty or broken lines

        times.append(t)
        temps.append(temp)
        hums.append(hum)
        lights.append(light)

    if len(times) < 2:
        return f"Not enough valid rows in {csv_file.name}\n"

    gaps = []
    for i in range(len(times) - 1):
        gaps.append(times[i + 1] - times[i])
    avg_gap = sum(gaps) / len(gaps)

    text = f"File: {csv_file.name}\n"
    text += f"Number of rows: {len(times)}\n"
    text += "\n=== Frequency ===\n"
    text += f"Average time between two results: {avg_gap:.2f} s\n"
    text += f"Min gap: {min(gaps):.2f} s   Max gap: {max(gaps):.2f} s\n"
    text += f"Frequency: {1 / avg_gap:.4f} Hz  ({60 / avg_gap:.2f} results per minute)\n"
    text += analyse("Temperature", temps)
    text += analyse("Humidity", hums)
    text += analyse("Light", lights)
    return text


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(CSV_DIR.glob("*.csv"))
    print(f"{len(csv_files)} csv files found")

    for csv_file in csv_files:
        result = analyse_file(csv_file)

        log_file = LOG_DIR / (csv_file.stem + ".log")
        log_file.write_text(result)
        print(f"done: {csv_file.name} -> {log_file.name}")


if __name__ == "__main__":
    main()
