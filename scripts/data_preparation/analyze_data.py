import glob
import os
import pandas as pd
import matplotlib.pyplot as plt

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
data_dir = f"{ROOT}/data/data_by_sensor_node/csv"
out_dir = f"{ROOT}/metrics_and_analysis/boxplots"
os.makedirs(out_dir, exist_ok=True)

# valid ranges, so sensor glitches don't squash the boxes
valid = {"temperature": (-10, 50), "humidity": (0, 100), "light": (0, 3000)}
units = {"temperature": "°C", "humidity": "%", "light": "lux"}

stats_rows = []

for path in sorted(glob.glob(f"{data_dir}/mote_*.csv"),
                   key=lambda p: int(os.path.basename(p)[5:-4])):
    mid = int(os.path.basename(path)[5:-4])
    df = pd.read_csv(path)

    fig, axes = plt.subplots(1, 3, figsize=(12, 5))
    row = {"mote": mid}

    for ax, (var, (lo, hi)) in zip(axes, valid.items()):
        s = df[var].where(df[var].between(lo, hi)).dropna()
        if s.empty:
            ax.set_title(f"{var} (no data)")
            continue

        ax.boxplot(s, showfliers=False)
        ax.set_title(f"{var} boxplot")
        ax.set_ylabel(units[var])
        ax.set_xticks([])

        # the text under each box, like in the sketch
        ax.set_xlabel(f"Max: {s.max():.2f}\n"
                      f"Min: {s.min():.2f}\n"
                      f"Average: {s.mean():.2f}\n"
                      f"Std dev: {s.std():.2f}")

        row.update({f"{var}_max": s.max(), f"{var}_min": s.min(),
                    f"{var}_avg": s.mean(), f"{var}_std": s.std()})

    stats_rows.append(row)
    fig.suptitle(f"Mote {mid}")
    plt.tight_layout()
    plt.savefig(f"{out_dir}/mote_{mid}.png", dpi=120)
    plt.close(fig)

pd.DataFrame(stats_rows).round(2).to_csv(f"{out_dir}/stats_per_mote.csv", index=False)
print(f"Done: {len(stats_rows)} motes")
