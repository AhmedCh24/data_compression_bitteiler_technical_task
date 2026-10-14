/*
 * ingestion_task.c - groups readings by sample ID into frames and compresses them
 *
 * A frame is closed when all three sensors arrived or after 3 polling periods.
 * The network queue send never waits, statistics are counted, and the
 * end-to-end latency is measured.
 */
#include "tasks.h"
#include "config.h"
#include "compression.h"
#include "rt_stats.h"
#include <string.h>

static uint64_t oldest_read_ns = 0;   /* oldest reading of the current frame */

static void update_frame(SensorFrame *frame, const SensorReading *r, bool seen[3])
{
    if (!r->valid || r->sensor > SENSOR_LIGHT) return;
    if (r->sensor == SENSOR_TEMPERATURE) frame->temperature_tick = r->value_tick;
    else if (r->sensor == SENSOR_HUMIDITY) {
        frame->humidity_tick = r->value_tick;
        frame->humidity_residual_1e4 = r->humidity_residual_1e4;
    } else frame->light_tick = r->value_tick;
    seen[r->sensor] = true;
    if (r->timestamp_ms > frame->timestamp_ms) frame->timestamp_ms = r->timestamp_ms;
    if (oldest_read_ns == 0 || r->read_ns < oldest_read_ns) oldest_read_ns = r->read_ns;
}

static void submit_sample(SensorFrame *frame, bool seen[3])
{
    static NetMessage msg;
    CompressedPacket packet;

    if (!compress_frames(frame, 1, &packet)) {
        g_counters.encode_errors++;
        return;
    }
    g_counters.frames++;
    if (seen[0] && seen[1] && seen[2]) g_counters.frames_complete++;
    else g_counters.frames_partial++;   /* a missing sensor kept its last value */
    if (packet.length == 28) g_counters.keyframes++;
    g_counters.bytes_compressed += packet.length;

    memcpy(msg.data, packet.data, packet.length);
    msg.length = packet.length;
    msg.port = UDP_SERVER_PORT;
    msg.queued_ns = mono_ns();
    msg.oldest_read_ns = oldest_read_ns;
    oldest_read_ns = 0;
    post(netQueue, &msg, false, &g_counters.net_drops, &g_counters.net_peak);
}

void ingestion_task(void *argument)
{
    SensorFrame current = {0}, last_values = {0};
    bool have_last_values = false, active = false, seen[3] = {false, false, false};
    (void)argument;

    for (;;) {
        SensorReading reading;
        JobTimer job;

        if (xQueueReceive(readingQueue, &reading, pdMS_TO_TICKS(SAMPLE_ASSEMBLY_PERIODS * g_period_ms)) == pdTRUE) {
            rt_job_start(&job, reading.read_ns);
            if (!active) {
                current = have_last_values ? last_values : (SensorFrame){0};
                current.sample_id = reading.sample_id;
                current.timestamp_ms = reading.timestamp_ms;
                active = true;
            } else if (reading.sample_id > current.sample_id) {
                submit_sample(&current, seen);   /* a newer sample closes the previous one */
                last_values = current;
                have_last_values = true;
                current.sample_id = reading.sample_id;
                current.timestamp_ms = reading.timestamp_ms;
                memset(seen, 0, sizeof(seen));
            } else if (reading.sample_id < current.sample_id) {
                rt_job_end(TASK_INGESTION, &job);
                continue;   /* stale reading */
            }
            update_frame(&current, &reading, seen);
            if (seen[0] && seen[1] && seen[2]) {   /* all sensors arrived */
                submit_sample(&current, seen);
                last_values = current;
                have_last_values = true;
                active = false;
                memset(seen, 0, sizeof(seen));
            }
            rt_job_end(TASK_INGESTION, &job);
        } else if (active) {
            /* timeout: missing sensors keep their last value */
            rt_job_start(&job, 0);
            submit_sample(&current, seen);
            last_values = current;
            have_last_values = true;
            active = false;
            memset(seen, 0, sizeof(seen));
            rt_job_end(TASK_INGESTION, &job);
        }
    }
}
