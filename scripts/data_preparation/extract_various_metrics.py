#!/usr/bin/env python3
import csv
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CSV_DIR = ROOT / "data/data_by_sensor_node/csv"                      # folder with your 54 csv files
OUT_FILE = ROOT / "metrics_and_analysis" / "summary.csv"


def to_seconds(date, hour):
    return datetime.strptime(f"{date} {hour}", "%Y-%m-%d %H:%M:%S.%f").timestamp()


def jumps(values):
    # returns (average absolute difference, biggest jump)
    abs_diffs = []
    for i in range(len(values) - 1):
        abs_diffs.append(abs(values[i + 1] - values[i]))
    return sum(abs_diffs) / len(abs_diffs), max(abs_diffs)


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
        return None

    gaps = []
    for i in range(len(times) - 1):
        gaps.append(times[i + 1] - times[i])
    avg_gap = sum(gaps) / len(gaps)

    temp_avg, temp_max = jumps(temps)
    hum_avg, hum_max = jumps(hums)
    light_avg, light_max = jumps(lights)

    return [
        csv_file.stem,
        len(times),
        round(avg_gap, 2),
        round(min(gaps), 2),
        round(max(gaps), 2),
        round(1 / avg_gap, 4),
        round(temp_avg, 4), round(temp_max, 4),
        round(hum_avg, 4), round(hum_max, 4),
        round(light_avg, 4), round(light_max, 4),
    ]


def main():


    csv_files = sorted(CSV_DIR.glob("*.csv"))
    print(f"{len(csv_files)} csv files found")

    with open(OUT_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "file", "rows",
            "avg_gap_s", "min_gap_s", "max_gap_s", "freq_hz",
            "temp_avg_abs_diff", "temp_max_jump",
            "hum_avg_abs_diff", "hum_max_jump",
            "light_avg_abs_diff", "light_max_jump",
        ])

        for csv_file in csv_files:
            row = analyse_file(csv_file)
            if row is None:
                print(f"skipped (not enough valid rows): {csv_file.name}")
                continue
            writer.writerow(row)
            print(f"done: {csv_file.name}")

    print(f"Summary written to {OUT_FILE}")


if __name__ == "__main__":
    main()
