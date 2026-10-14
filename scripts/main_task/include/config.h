#ifndef APP_CONFIG_H
#define APP_CONFIG_H
/* All settings in one place. Reasons: see SCHEDULING_DESIGN.md. */

/* ---- Sensor files: the Python feeder writes the latest row here ---- */
#ifndef APP_DATA_DIR
#define APP_DATA_DIR "data/"                /* set to an absolute path by CMake */
#endif
#define TEMP_FILE APP_DATA_DIR "temp.txt"
#define HUMIDITY_FILE APP_DATA_DIR "humidity.txt"
#define LIGHT_FILE APP_DATA_DIR "light.txt"

/* ---- Polling period ----
 * The feeder overwrites one "latest value" per file, so a row is lost if two rows
 * are written between two polls. The period is set in DATASET time: poll every
 * 10 dataset-seconds, i.e. ~3 times per nominal 31 s sample. Then the share of
 * rows at risk (dataset gaps shorter than 10 s) is 0.23 % at ANY replay speed.
 * Real-time period = 10 s / speed:  1x -> 10 s, 30x -> 333 ms, 300x -> 33 ms.
 * The node must be started with the same --speed as the feeder. */
#define DATASET_POLL_INTERVAL_MS 10000
#define DEFAULT_REPLAY_SPEED 30           /* same default as the feeder */
#define MIN_PERIOD_MS 5                   /* below this, Linux jitter (~1-5 ms) dominates */

/* ---- Deadlines and priorities ----
 * The three sensor tasks have the same period, the same data rate and the same
 * work (read one file), so Rate-Monotonic gives them the SAME priority, with an
 * implicit deadline = period. The event handler is above them (sporadic, 2 ms
 * deadline); processing, network and monitoring are below them (soft deadlines). */
#define LIGHT_EVENT_DEADLINE_US 2000      /* event handling */
/* sensor deadline = period; ingestion (soft) deadline = period, both set at start-up */
#define UDP_DEADLINE_MS 100               /* soft: datagram queued -> sent */
#define HEALTH_PERIOD_MS 1000

#define PRIO_LIGHT_EVENT 5
#define PRIO_SENSORS 4                    /* light, temperature, humidity */
#define PRIO_INGESTION 3
#define PRIO_UDP 2
#define PRIO_HEALTH 1

/* ---- Frame assembly ---- */
#define SAMPLE_ASSEMBLY_PERIODS 3         /* frame timeout = 3 polling periods */

/* ---- Light event: threshold with hysteresis (on above 111 lux, off below 101 lux) ---- */
#define LIGHT_EVENT_THRESHOLD 106.0f
#define LIGHT_EVENT_HYSTERESIS 5.0f

/* ---- Queues ---- */
#define READING_QUEUE_LENGTH 96
#define NET_QUEUE_LENGTH 16
#define LIGHT_EVENT_QUEUE_LENGTH 8

/* ---- Network ---- */
#define UDP_SERVER_IP "127.0.0.1"
#define UDP_SERVER_PORT 5000      /* compressed packets */
#define RAW_UDP_PORT 5001         /* raw rows, sent by the feeder */
#define TELEMETRY_PORT 5002       /* health reports and light events (JSON) */
#endif
