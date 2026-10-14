#!/usr/bin/env python3
"""Lossless benchmark on the first 43,000 data rows of mote_7.csv.
Install: pip install pandas numpy zstandard
Run: python plotting_data.py --input ../../data/mote_7.csv --rows 43000 --output benchmark_output
"""
import argparse, json, logging, struct, sys, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
import numpy as np
import pandas as pd

try:
    import zstandard as zstd
except ImportError:
    sys.exit("Install dependencies first: pip install pandas numpy zstandard")

# Minimum preferred scales. The script raises a scale automatically when needed
# so decimal values are represented exactly instead of being rounded.
PREFERRED_SCALES = {"temperature": 10000, "humidity": 10000, "light": 100}
FEATURES = list(PREFERRED_SCALES)

def scaled_ints(series, preferred_scale, feature):
    decimals = []
    for row, value in enumerate(series, 1):
        if pd.isna(value):
            raise ValueError(f"{feature}: missing value at data row {row}")
        try:
            x = Decimal(str(value))
        except InvalidOperation as e:
            raise ValueError(f"{feature}: invalid value {value!r} at row {row}") from e
        if not x.is_finite():
            raise ValueError(f"{feature}: non-finite value {value!r} at row {row}")
        decimals.append(x)

    # Determine the decimal precision needed to represent every value exactly.
    # Decimal exponent can be negative (e.g. 0.0201102 has exponent -7).
    max_places = max(max(0, -x.as_tuple().exponent) for x in decimals)
    preferred_places = len(str(preferred_scale)) - 1
    places = max(preferred_places, max_places)
    scale = 10 ** places

    out = []
    for row, x in enumerate(decimals, 1):
        scaled = x * Decimal(scale)
        if scaled != scaled.to_integral_value():
            raise ValueError(
                f"{feature}: failed exact integer conversion at data row {row}: {x}"
            )
        out.append(int(scaled))
    values = np.asarray(out, dtype=np.int64)
    return values, scale

def int_bytes(x):
    return np.asarray(x, dtype="<i8").tobytes()

def delta_encode(x):
    x = np.asarray(x, dtype=np.int64)
    d = np.empty_like(x)
    if len(x):
        d[0] = x[0]
        d[1:] = x[1:] - x[:-1]
    return d

def delta_decode(b):
    return np.cumsum(np.frombuffer(b, dtype="<i8"), dtype=np.int64)

def rle_encode(x):
    x = np.asarray(x, dtype=np.int64)
    out, i = bytearray(), 0
    while i < len(x):
        j = i + 1
        while j < len(x) and x[j] == x[i] and j-i < 0xffffffff:
            j += 1
        out.extend(struct.pack("<qI", int(x[i]), j-i))
        i = j
    return bytes(out)

def rle_decode(b, n):
    if len(b) % 12:
        raise ValueError("Invalid RLE payload length")
    out = []
    for i in range(0, len(b), 12):
        value, count = struct.unpack_from("<qI", b, i)
        if count == 0 or len(out) + count > n:
            raise ValueError("Invalid RLE run length")
        out.extend([value] * count)
    if len(out) != n:
        raise ValueError(f"RLE restored {len(out)} values, expected {n}")
    return np.asarray(out, dtype=np.int64)

def xor_encode(x):
    bits = np.asarray(x, dtype="<f8").view("<u8")
    out = np.empty_like(bits)
    if len(bits):
        out[0] = bits[0]
        out[1:] = bits[1:] ^ bits[:-1]
    return out.astype("<u8", copy=False).tobytes()

def xor_decode(b, n):
    x = np.frombuffer(b, dtype="<u8")
    if len(x) != n:
        raise ValueError("Invalid XOR payload size")
    bits = np.empty_like(x)
    if len(x):
        bits[0] = x[0]
        for i in range(1, len(x)):
            bits[i] = bits[i-1] ^ x[i]
    return bits.astype("<u8", copy=False).view("<f8").copy()

def run_stats(x):
    if len(x) == 0:
        return {"unique_values": 0, "runs": 0, "adjacent_repeat_pct": 0.0,
                "mean_run_length": 0.0, "max_run_length": 0}
    starts = np.flatnonzero(np.r_[True, x[1:] != x[:-1]])
    lengths = np.diff(np.r_[starts, len(x)])
    return {
        "unique_values": int(np.unique(x).size),
        "runs": int(len(lengths)),
        "adjacent_repeat_pct": float(100*np.mean(x[1:] == x[:-1])) if len(x)>1 else 0.0,
        "mean_run_length": float(np.mean(lengths)),
        "max_run_length": int(lengths.max()),
    }

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=str(Path(__file__).resolve().parents[2] / "data" / "mote_7.csv"))
    ap.add_argument("--rows", type=int, default=43000)
    ap.add_argument("--output", default=str(Path(__file__).resolve().parent / "benchmark_output"))
    ap.add_argument("--zstd-level", type=int, default=9)
    a = ap.parse_args()
    if a.rows < 1 or not 1 <= a.zstd_level <= 22:
        ap.error("--rows must be positive and --zstd-level must be 1..22")

    outdir = Path(a.output)
    cdir = outdir / "compressed"
    cdir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("benchmark")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(outdir / "benchmark.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)

    infile = Path(a.input)
    if not infile.exists():
        log.error("Input file not found: %s", infile.resolve())
        log.error("Use --input to specify the path to mote_7.csv")
        return 2

    # nrows prevents any records after the requested prefix from entering the experiment.
    df = pd.read_csv(infile, nrows=a.rows)
    if not len(df):
        log.error("No data rows found")
        return 2
    missing = [f for f in FEATURES if f not in df.columns]
    if missing:
        log.error("Missing columns %s; available columns: %s", missing, list(df.columns))
        return 2

    log.info("Input: %s", infile.resolve())
    log.info("Rows tested: %d (first %d data rows; header excluded)", len(df), a.rows)
    log.info("Zstandard level: %d", a.zstd_level)
    if len(df) < a.rows:
        log.warning("File had fewer rows than requested.")
    for c in ("date", "time"):
        if c in df.columns:
            log.info("%s range: %s to %s", c, df[c].iloc[0], df[c].iloc[-1])

    zc, zd = zstd.ZstdCompressor(level=a.zstd_level), zstd.ZstdDecompressor()
    results, report = [], {
        "input_file": str(infile.resolve()), "requested_rows": a.rows,
        "actual_rows": len(df), "zstd_level": a.zstd_level, "features": {},
        "notes": [
            "Only the first actual_rows records were read; header excluded.",
            "Compressed sizes are payload sizes, excluding small container/metadata overhead.",
            "All methods are decompressed and checked for exact reconstruction.",
            "XOR method is bitwise-lossless for the float64 values parsed by pandas.",
            "Integer scales are inferred from decimal precision in the tested prefix, with preferred minimum scales temperature=10000, humidity=10000, light=100; values are never rounded."
        ]
    }

    def evaluate(feature, method, source, transform, inverse, representation):
        t0 = time.perf_counter()
        payload = transform(source)
        packed = zc.compress(payload)
        enc_s = time.perf_counter() - t0
        artifact = cdir / f"{feature}_{method}.zst"
        artifact.write_bytes(packed)
        t0 = time.perf_counter()
        restored = inverse(zd.decompress(packed))
        dec_s = time.perf_counter() - t0
        if source.dtype.kind == "f":
            exact = np.array_equal(source.view(np.uint64), restored.view(np.uint64))
        else:
            exact = np.array_equal(source, restored)
        if not exact:
            raise AssertionError(f"LOSSLESS CHECK FAILED: {feature}/{method}")
        original_bytes = len(source) * 8
        compressed_bytes = len(packed)
        r = {
            "feature": feature, "method": method, "representation": representation,
            "samples": len(source), "logical_original_bytes": original_bytes,
            "transformed_bytes_before_zstd": len(payload),
            "compressed_bytes": compressed_bytes,
            "compression_ratio": original_bytes/compressed_bytes if compressed_bytes else None,
            "space_saving_pct": 100*(1-compressed_bytes/original_bytes) if original_bytes else 0,
            "encode_seconds": enc_s, "decode_seconds": dec_s,
            "encode_samples_per_second": len(source)/enc_s if enc_s else None,
            "decode_samples_per_second": len(source)/dec_s if dec_s else None,
            "lossless_verified": bool(exact), "artifact": str(artifact)
        }
        results.append(r)
        log.info("%s | %-18s | %7d B | ratio %.3fx | saved %.2f%% | enc %.4fs | dec %.4fs | exact=%s",
                 feature, method, compressed_bytes, r["compression_ratio"],
                 r["space_saving_pct"], enc_s, dec_s, exact)

    try:
        for feature, preferred_scale in PREFERRED_SCALES.items():
            log.info("----- %s -----", feature)
            ints, scale = scaled_ints(df[feature], preferred_scale, feature)
            log.info("Exact decimal integer scale selected: %d (preferred minimum: %d)",
                     scale, preferred_scale)
            stats = run_stats(ints)
            report["features"][feature] = {
                "scale": scale, "min_value": int(ints.min())/scale,
                "max_value": int(ints.max())/scale,
                "mean_value": float(ints.mean())/scale,
                "std_value_population": float(ints.std())/scale, **stats
            }
            log.info("scale=%d min=%g max=%g mean=%g unique=%d runs=%d adjacent repeats=%.2f%% max_run=%d",
                     scale, ints.min()/scale, ints.max()/scale, ints.mean()/scale,
                     stats["unique_values"], stats["runs"],
                     stats["adjacent_repeat_pct"], stats["max_run_length"])
            evaluate(feature, "raw_zstd", ints, int_bytes,
                     lambda b: np.frombuffer(b, dtype="<i8").copy(),
                     f"scaled int64, scale={scale}")
            if feature in ("temperature", "humidity"):
                evaluate(feature, "delta_zstd", ints,
                         lambda x: int_bytes(delta_encode(x)), delta_decode,
                         f"delta scaled int64, scale={scale}")
            if feature == "light":
                evaluate(feature, "rle_zstd", ints, rle_encode,
                         lambda b, n=len(ints): rle_decode(b, n),
                         f"RLE scaled int64, scale={scale}; 12 bytes/run before Zstd")
            floats = pd.to_numeric(df[feature], errors="raise").to_numpy(dtype=np.float64)
            evaluate(feature, "xor_float64_zstd", floats, xor_encode,
                     lambda b, n=len(floats): xor_decode(b, n),
                     "float64 bitwise XOR with previous sample")
    except Exception:
        log.exception("Benchmark failed")
        return 1

    report["results"] = results
    pd.DataFrame(results).to_csv(outdir / "results.csv", index=False)
    (outdir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("All lossless checks passed.")
    log.info("Created: %s", (outdir / "results.csv").resolve())
    log.info("Created: %s", (outdir / "report.json").resolve())
    log.info("Created: %s", (outdir / "benchmark.log").resolve())
    log.info("Compressed artifacts: %s", cdir.resolve())
    log.info("Send me results.csv, report.json, and benchmark.log for analysis.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
