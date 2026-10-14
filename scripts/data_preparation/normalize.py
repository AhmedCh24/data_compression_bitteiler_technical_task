from pathlib import Path
import pandas as pd
data_dir = Path(__file__).resolve().parents[2] / "data/data_by_sensor_node"
# Read the input CSV file
id=32
df = pd.read_csv(f"{data_dir}/csv/mote_{id}.csv")

# Calculate differences from the previous row
df["delta_temp"] = df["temperature"].diff()
df["delta_humidity"] = df["humidity"].diff()
df["delta_light"] = df["light"].diff()

# Calculate steps
df["steps_temp"] = df["delta_temp"] / 0.0098
df["steps_humidity"] = df["delta_humidity"] / 0.034
df["steps_light"] = df["delta_light"] / 0.46

# Calculate absolute values of steps
df["absolute_steps_temp"] = df["steps_temp"].abs()
df["absolute_steps_humidity"] = df["steps_humidity"].abs()
df["absolute_steps_light"] = df["steps_light"].abs()

# Save the result
df.to_csv(f"{data_dir}/csv_normalized/mote_{id}_normalized.csv", index=False)


