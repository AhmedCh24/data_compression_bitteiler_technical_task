import os
import pandas as pd

cols = ["date", "time", "epoch", "moteid",
        "temperature", "humidity", "light", "voltage"]

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
output_dir = f"{ROOT}/data/data_by_sensor_node"
os.makedirs(f"{output_dir}/csv", exist_ok=True)
os.makedirs(f"{output_dir}/txt", exist_ok=True)

df = pd.read_csv(f"{ROOT}/data/data.txt", sep=r"\s+", names=cols,
                 header=None, on_bad_lines="skip")

df = df.dropna(subset=["moteid"])
df["moteid"] = df["moteid"].astype(int)
df = df[df["moteid"].between(1, 54)]   # keep only valid motes

df["timestamp"] = pd.to_datetime(df["date"] + " " + df["time"], errors="coerce")
df = df.dropna(subset=["timestamp"])

df = df.sort_values(["moteid", "timestamp", "epoch"], kind="stable")

for mid, g in df.groupby("moteid"):
    g = g.drop(columns="timestamp")

    # CSV version ( here i did it because i think it's better format to analyze data using python
    g.to_csv(f"{output_dir}/csv/mote_{mid}.csv", index=False)

    # TXT version: (here i'm thinking about using them as the sensor simulator code reads directly from the txt file line by line "initial thoughts")
    g.to_csv(f"{output_dir}/txt/mote_{mid}.txt",
             header=False, index=False, sep="\t")


