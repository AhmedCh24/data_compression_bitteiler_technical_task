/*
 * sensor_tasks.c - the three sensor tasks and the light event task
 *
 * Each sensor task polls its own file once per polling period (sensor_read_latest).
 * A new sample ID means a new value: it is quantized to sensor ticks and sent to
 * the ingestion task.
 */
#include "tasks.h"
#include "config.h"
#include "rt_stats.h"
#include "sensor_io.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

QueueHandle_t readingQueue = NULL;
QueueHandle_t lightEventQueue = NULL;
Counters g_counters;

static const char *const files[3] = { TEMP_FILE, HUMIDITY_FILE, LIGHT_FILE };
static const TaskId task_ids[3] = { TASK_TEMPERATURE, TASK_HUMIDITY, TASK_LIGHT };

/* Convert a physical value to integer sensor ticks */
static bool quantize(SensorKind kind, float value, SensorReading *r)
{
    long tick;
    if (kind == SENSOR_TEMPERATURE) {
        tick = lroundf(value / 0.0098f);
    } else if (kind == SENSOR_HUMIDITY) {
        long residual;
        tick = lroundf(value / 0.34f);
        residual = lroundf((value - 0.34f * (float)tick) * 10000.0f);
        if (residual < INT16_MIN || residual > INT16_MAX) return false;
        r->humidity_residual_1e4 = (int16_t)residual;
    } else {
        tick = lroundf(value / 0.46f);
    }
    if (tick < 0 || tick > UINT16_MAX) return false;
    r->value_tick = (uint16_t)tick;
    return true;
}

/* Light threshold with hysteresis: send an event to the light event task */
static void check_light(float lux, uint64_t sample_id)
{
    static bool known = false, above = false;
    LightEvent ev;

    if (!known) {
        above = lux >= LIGHT_EVENT_THRESHOLD;
        known = true;
        return;
    }
    if ((!above && lux >= LIGHT_EVENT_THRESHOLD + LIGHT_EVENT_HYSTERESIS) ||
        (above && lux < LIGHT_EVENT_THRESHOLD - LIGHT_EVENT_HYSTERESIS)) {
        above = !above;
        ev.sample_id = sample_id;
        ev.detect_ns = mono_ns();
        ev.lux = lux;
        ev.rising = above;
        xQueueSend(lightEventQueue, &ev, 0);
    }
}

static void acquisition_loop(SensorKind kind)
{
    TaskId id_task = task_ids[kind];
    TickType_t last_wake = xTaskGetTickCount();
    uint64_t last_id = UINT64_MAX;

    while (1) {
        JobTimer job;
        uint64_t id, timestamp;
        float value;

        /* pdFALSE = the next release time had already passed: overrun */
        if (xTaskDelayUntil(&last_wake, pdMS_TO_TICKS(g_period_ms)) == pdFALSE) {
            rt_overrun(id_task);
        }
        rt_job_start(&job, rt_tick_time_ns(last_wake));
        g_counters.polls[kind]++;

        if (sensor_read_latest(files[kind], &id, &timestamp, &value) && id != last_id) {
            SensorReading r;
            memset(&r, 0, sizeof(r));
            r.sample_id = id;
            r.timestamp_ms = timestamp;
            r.sensor = kind;
            r.valid = quantize(kind, value, &r);
            r.read_ns = mono_ns();
            if (kind == SENSOR_LIGHT) {
                check_light(value, id);
            }
            /* never wait: if the queue is full, count it and retry on the next poll */
            if (post(readingQueue, &r, false, &g_counters.reading_drops, &g_counters.reading_peak)) {
                g_counters.new_values[kind]++;
                last_id = id;
            }
        }
        rt_job_end(id_task, &job);
    }
}

void temperature_acquisition_task(void *argument) { (void)argument; acquisition_loop(SENSOR_TEMPERATURE); }
void humidity_acquisition_task(void *argument) { (void)argument; acquisition_loop(SENSOR_HUMIDITY); }
void light_acquisition_task(void *argument) { (void)argument; acquisition_loop(SENSOR_LIGHT); }

/* Light event task (highest priority): sends an alert to the dashboard at once */
void light_event_task(void *argument)
{
    LightEvent ev;
    (void)argument;

    while (1) {
        JobTimer job;
        NetMessage msg;
        uint64_t latency;

        xQueueReceive(lightEventQueue, &ev, portMAX_DELAY);
        rt_job_start(&job, ev.detect_ns);
        g_counters.light_events++;

        latency = mono_ns() - ev.detect_ns;
        memset(&msg, 0, sizeof(msg));
        msg.length = (uint16_t)snprintf((char *)msg.data, sizeof(msg.data),
            "{\"type\":\"event\",\"dir\":\"%s\",\"lux\":%.2f,\"sid\":%llu,\"latency_us\":%.1f}",
            ev.rising ? "rising" : "falling", ev.lux, (unsigned long long)ev.sample_id, latency / 1000.0);
        msg.port = TELEMETRY_PORT;
        msg.queued_ns = mono_ns();
        post(netQueue, &msg, true, &g_counters.net_drops, &g_counters.net_peak);   /* front: urgent */

        latency = mono_ns() - ev.detect_ns;
        if (latency > g_counters.event_latency_max_ns) {
            g_counters.event_latency_max_ns = latency;
        }
        rt_job_end(TASK_LIGHT_EVENT, &job);
    }
}
