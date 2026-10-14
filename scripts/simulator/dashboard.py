#!/usr/bin/env python3
"""Dashboard for raw mote_7.csv rows and FreeRTOS-compressed packets.

The protocol uses a 28-byte absolute keyframe whenever a stream starts or a delta
cannot be represented, followed by 6-byte delta datagrams. The raw feed is used
for per-sample source timestamps and accuracy comparison; timestamps are not
repeated in the compact delta packet.
"""
import json
import select
import socket
import struct
import time
from datetime import datetime

HOST = "0.0.0.0"
COMPRESSED_PORT = 5000
RAW_PORT = 5001
TELEMETRY_PORT = 5002    # node health report (JSON, every second)
HEALTH = {"report": None, "events": 0}
RAW_FORMAT = "!4sQqffff"
RAW_MAGIC = b"RAW1"
MAGIC = b"FR"
VERSION = 7
SENSOR_BYTES_PER_DELTA = 5
KEYFRAME_SENSOR_BYTES = 8
IPV4_UDP_HEADER_BYTES = 28  # estimate only: 20-byte IPv4 + 8-byte UDP
_decoder_state = None


def _signed(value, width):
    sign = 1 << (width - 1)
    return value - (1 << width) if value & sign else value


def decode(packet):
    """Return (decoded_frames, is_keyframe) for one compressed datagram."""
    global _decoder_state
    if len(packet) == 28 and packet[:2] == MAGIC and packet[2] == VERSION and packet[3] == 0:
        sid, ts, t, h, hres, light = struct.unpack_from("<QQHHhH", packet, 4)
        _decoder_state = {"sid": sid, "ts": ts, "t": t, "h": h, "hres": hres, "light": light}
        return [(sid, ts, t, h, hres, light)], True
    if len(packet) != 6 or _decoder_state is None:
        raise ValueError("invalid delta packet or keyframe not received")

    sid_delta = packet[0]
    if sid_delta == 0:
        raise ValueError("delta packet has zero sample-ID delta; expected keyframe")
    bits = int.from_bytes(packet[1:6], "big")
    dlight = _signed((bits >> 27) & 0x1FFF, 13)
    dhum = _signed((bits >> 16) & 0x7FF, 11)
    hres_q = _signed((bits >> 11) & 0x1F, 5)
    dtemp = _signed(bits & 0x7FF, 11)
    st = _decoder_state
    sid = st["sid"] + sid_delta
    t, h, light = st["t"] + dtemp, st["h"] + dhum, st["light"] + dlight
    hres = hres_q * 1000
    if not (0 <= t <= 65535 and 0 <= h <= 65535 and 0 <= light <= 65535 and -32768 <= hres <= 32767):
        raise ValueError("decoded sensor value out of range")
    # A delta packet intentionally omits timestamp; raw feed supplies it by ID.
    _decoder_state = {"sid": sid, "ts": None, "t": t, "h": h, "hres": hres, "light": light}
    return [(sid, None, t, h, hres, light)], False


def _timestamp_text(timestamp_ms):
    if timestamp_ms is None:
        return "timestamp pending"
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _record_match(sid, raw, decoded, stats, matched):
    """Reconcile asynchronous raw/compressed sockets exactly once per sample ID."""
    if sid not in raw or sid not in decoded or sid in matched:
        return
    rv, dv = raw[sid], decoded[sid]
    dv["ts"] = rv["ts"]
    # This is arrival-time separation between two UDP feeds, not sensor latency.
    stats["arrival_separations_ms"].append(abs((dv["arrival"] - rv["arrival"]) * 1000.0))
    errors = {
        "temperature": abs(dv["temp"] - rv["temp"]),
        "humidity": abs(dv["hum"] - rv["hum"]),
        "light": abs(dv["light"] - rv["light"]),
    }
    for name, error in errors.items():
        stats["error_sum"][name] += error
        stats["error_max"][name] = max(stats["error_max"][name], error)
    stats["matched_samples"] += 1
    matched.add(sid)


def show(raw, decoded, stats, counters, last_events):
    print("\033[2J\033[H", end="")
    print("=" * 82)
    print("          MOTE 7 — FREERTOS FIXED-WIDTH STREAM DASHBOARD")
    print("=" * 82)
    print("Latest dataset epoch | Source timestamp | Raw CSV values vs decoded values")
    ids = sorted(set(raw) | set(decoded))
    for sid in ids[-8:]:
        rv, dv = raw.get(sid), decoded.get(sid)
        when = _timestamp_text(rv["ts"] if rv else (dv["ts"] if dv else None))
        raw_text = (f"T={rv['temp']:.4f}C H={rv['hum']:.4f}% L={rv['light']:.2f}lux V={rv['voltage']:.5f}V"
                    if rv else "raw packet pending")
        dec_text = (f"T={dv['temp']:.4f}C H={dv['hum']:.4f}% L={dv['light']:.2f}lux"
                    if dv else "compressed packet pending")
        print(f"epoch {sid:>6} | {when}")
        print(f"  RAW : {raw_text}")
        print(f"  DECO: {dec_text}")

    print("-" * 82)
    raw_bytes = stats["sensor_raw_bytes"]
    sensor_bytes = stats["sensor_encoded_bytes"]
    payload_bytes = stats["udp_payload_bytes"]
    ratio = raw_bytes / sensor_bytes if sensor_bytes else 0.0
    saved = raw_bytes - sensor_bytes
    reduction = (100.0 * saved / raw_bytes) if raw_bytes else 0.0
    print("COMPRESSION (same decoded sample count; sensor fields only)")
    print(f"  decoded samples      : {counters['frames']}")
    print(f"  raw sensor baseline  : {raw_bytes} bytes (8 bytes/sample)")
    print(f"  encoded sensor data  : {sensor_bytes} bytes (8 per keyframe, 5 per delta)")
    print(f"  sensor ratio         : {ratio:.3f}x")
    print(f"  sensor bytes saved   : {saved} ({reduction:.1f}%)")
    print(f"  keyframes / deltas   : {counters['keyframes']} / {counters['delta_packets']}")
    print(f"  app UDP payload      : {payload_bytes} bytes (includes protocol metadata; excludes network headers)")
    print(f"  IPv4+UDP estimate    : {payload_bytes + counters['packets'] * IPV4_UDP_HEADER_BYTES} bytes (28-byte headers/datagram; no Ethernet)")
    print(f"  packets / errors     : {counters['packets']} / {counters['errors']}")
    print(f"  raw datagrams/IDs    : {counters['raw_datagrams']} / {len(raw)} unique IDs")
    print(f"  unmatched raw/decoded: {len(set(raw) - set(decoded))} / {len(set(decoded) - set(raw))}")
    if stats["arrival_separations_ms"]:
        values = stats["arrival_separations_ms"]
        print(f"  raw/compressed arrival separation: avg={sum(values)/len(values):.1f} ms max={max(values):.1f} ms")
    if stats["matched_samples"]:
        n = stats["matched_samples"]
        print(f"  accuracy over {n} matched samples (decoded vs raw):")
        for name, unit in (("temperature", "°C"), ("humidity", "percentage points"), ("light", "lux")):
            print(f"    {name:11s} MAE={stats['error_sum'][name]/n:.5f} {unit}; max abs error={stats['error_max'][name]:.5f} {unit}")
    else:
        print("  accuracy             : waiting for matched raw/decoded samples")
    print(f"  light events         : {last_events['count']} (threshold crossings at 106 lux, raw feed)")
    h = HEALTH["report"]
    print("-" * 82)
    if h is None:
        print("NODE HEALTH: waiting for telemetry on UDP 5002")
    else:
        print(f"NODE HEALTH (uptime {h['up_s']:.0f} s, tick {h['tick_hz']:.0f} Hz) - scheduling: "
              f"U={h['sched']['u']:.4f} Ud={h['sched']['ud']:.4f} bound={h['sched']['bound']:.4f}")
        print(f"  {'task':16s}{'prio':>5s}{'jobs':>7s}{'miss':>6s}{'ovr':>5s}{'Cmax us':>10s}{'Jmax us':>10s}{'Rmax us':>10s}")
        for t in h["tasks"]:
            print(f"  {t[0]:16s}{t[1]:5d}{t[4]:7d}{t[5]:6d}{t[6]:5d}{t[8]:10.1f}{t[9]:10.1f}{t[10]:10.1f}")
        q = h["q"]
        print(f"  queues: reading peak {q['reading'][1]}/{q['reading'][2]} drops {q['reading'][3]} | "
              f"net peak {q['net'][1]}/{q['net'][2]} drops {q['net'][3]} | frames partial {h['frames']['partial']} | "
              f"e2e {h['lat']['e2e_avg_ms']:.2f}/{h['lat']['e2e_max_ms']:.2f} ms | node light events {HEALTH['events']}")
    print(f"Listening: compressed UDP {HOST}:{COMPRESSED_PORT}; raw UDP {HOST}:{RAW_PORT}; telemetry UDP {HOST}:{TELEMETRY_PORT}")
    print("Delta packets omit timestamps; the raw feed matches them by sample ID.")
    print("Humidity residual is quantized to 0.1 percentage-point units in delta packets.")


def main():
    comp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    comp.bind((HOST, COMPRESSED_PORT))
    raw_sock.bind((HOST, RAW_PORT))
    tel_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tel_sock.bind((HOST, TELEMETRY_PORT))
    raw, decoded, matched = {}, {}, set()
    stats = {
        "sensor_raw_bytes": 0,
        "sensor_encoded_bytes": 0,
        "udp_payload_bytes": 0,
        "arrival_separations_ms": [],
        "error_sum": {"temperature": 0.0, "humidity": 0.0, "light": 0.0},
        "error_max": {"temperature": 0.0, "humidity": 0.0, "light": 0.0},
        "matched_samples": 0,
    }
    counters = {"frames": 0, "packets": 0, "errors": 0, "raw_datagrams": 0,
                "keyframes": 0, "delta_packets": 0}
    last_events = {"count": 0, "previous_light": None}
    sockets = [comp, raw_sock, tel_sock]
    print(f"Dashboard ready: compressed={HOST}:{COMPRESSED_PORT}, raw={HOST}:{RAW_PORT}")
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [])
            for sock in readable:
                packet, _ = sock.recvfrom(8192)
                arrival = time.monotonic()
                if sock is tel_sock:
                    try:
                        msg = json.loads(packet)
                        if msg.get("type") == "health":
                            HEALTH["report"] = msg
                        elif msg.get("type") == "event":
                            HEALTH["events"] += 1
                    except ValueError:
                        counters["errors"] += 1
                elif sock is raw_sock:
                    try:
                        magic, sid, ts, temp, hum, light, voltage = struct.unpack(RAW_FORMAT, packet)
                        if magic != RAW_MAGIC:
                            raise ValueError("invalid raw packet magic")
                        counters["raw_datagrams"] += 1
                        raw[sid] = {"ts": ts, "temp": temp, "hum": hum, "light": light,
                                    "voltage": voltage, "arrival": arrival}
                        previous_light = last_events["previous_light"]
                        if previous_light is not None and ((previous_light < 106.0 <= light) or (previous_light >= 106.0 > light)):
                            last_events["count"] += 1
                        last_events["previous_light"] = light
                        _record_match(sid, raw, decoded, stats, matched)
                    except (struct.error, ValueError):
                        counters["errors"] += 1
                else:
                    try:
                        frames, is_keyframe = decode(packet)
                        counters["packets"] += 1
                        counters["frames"] += len(frames)
                        if is_keyframe:
                            counters["keyframes"] += 1
                        else:
                            counters["delta_packets"] += 1
                        stats["sensor_raw_bytes"] += len(frames) * 8
                        stats["sensor_encoded_bytes"] += len(frames) * (KEYFRAME_SENSOR_BYTES if is_keyframe else SENSOR_BYTES_PER_DELTA)
                        stats["udp_payload_bytes"] += len(packet)
                        for sid, ts, t, h, hres, light in frames:
                            decoded[sid] = {
                                "ts": raw[sid]["ts"] if sid in raw else ts,
                                "temp": t * 0.0098,
                                "hum": h * 0.34 + hres / 10000.0,
                                "light": light * 0.46,
                                "arrival": arrival,
                            }
                            _record_match(sid, raw, decoded, stats, matched)
                    except (ValueError, struct.error, OverflowError):
                        counters["errors"] += 1
            show(raw, decoded, stats, counters, last_events)
    finally:
        comp.close()
        raw_sock.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
