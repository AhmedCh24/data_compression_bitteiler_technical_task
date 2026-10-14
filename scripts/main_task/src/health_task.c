/*
 * health_task.c - system health, every second (lowest priority)
 *
 *   - sends a JSON report to the dashboard (UDP 5002): task timing, schedulability,
 *     queues, sensors, frames, latencies, events
 *   - prints one status line (it is the only task that prints)
 *   - stops the program on Ctrl+C or after --duration, with a final summary
 */
#include "tasks.h"
#include "config.h"
#include "rt_stats.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PERIODIC_TASKS 4   /* light, temperature, humidity, health */

static double us(uint64_t ns) { return ns / 1000.0; }

static double avg_exec_us(const TaskStats *s)
{
    return s->jobs ? us(s->exec_sum_ns) / s->jobs : 0.0;
}

/* Schedulability (sufficient test with measured worst-case execution times):
 *   utilisation  U  = sum(Cmax / T)
 *   density      Ud = sum(Cmax / D)   (equal to U when every deadline = period)
 *   both must stay below n * (2^(1/n) - 1) */
static void schedulability(const TaskStats *s, double *u, double *ud)
{
    int i;
    *u = 0.0;
    *ud = 0.0;
    for (i = 0; i < TASK_COUNT; i++) {
        if (s[i].period_ms > 0) {
            *u += s[i].exec_max_ns / (s[i].period_ms * 1e6);
            *ud += s[i].exec_max_ns / (s[i].deadline_us * 1e3);
        }
    }
}

static double bound(void)
{
    return PERIODIC_TASKS * (pow(2.0, 1.0 / PERIODIC_TASKS) - 1.0);
}

static int build_report(char *t, int size, double up_s, double tick_hz, const double cpu[])
{
    TaskStats s[TASK_COUNT];
    Counters c = g_counters;
    double u, ud;
    int n = 0, i;

    rt_snapshot(s);
    schedulability(s, &u, &ud);

    n += snprintf(t + n, size - n, "{\"type\":\"health\",\"up_s\":%.1f,\"tick_hz\":%.1f,\"speed\":%u,\"period_ms\":%u,\"tasks\":[",
                  up_s, tick_hz, (unsigned)g_speed, (unsigned)g_period_ms);
    /* per task: name, prio, period ms, deadline us, jobs, misses, overruns,
     *           exec avg, exec max, jitter max, response max (us), cpu % */
    for (i = 0; i < TASK_COUNT; i++) {
        n += snprintf(t + n, size - n, "%s[\"%s\",%u,%u,%u,%u,%u,%u,%.1f,%.1f,%.1f,%.1f,%.2f]",
                      i ? "," : "", s[i].name, s[i].priority, (unsigned)s[i].period_ms,
                      (unsigned)s[i].deadline_us, (unsigned)s[i].jobs, (unsigned)s[i].deadline_misses,
                      (unsigned)s[i].overruns, avg_exec_us(&s[i]), us(s[i].exec_max_ns),
                      us(s[i].jitter_max_ns), us(s[i].response_max_ns), cpu[i]);
    }
    n += snprintf(t + n, size - n,
        "],\"sched\":{\"u\":%.5f,\"ud\":%.5f,\"bound\":%.4f},"
        "\"q\":{\"reading\":[%u,%u,%d,%u],\"net\":[%u,%u,%d,%u]},"
        "\"sensors\":{\"polls\":[%u,%u,%u],\"new\":[%u,%u,%u]},"
        "\"frames\":{\"total\":%u,\"complete\":%u,\"partial\":%u,\"keyframes\":%u,\"bytes\":%llu,\"errors\":%u},"
        "\"lat\":{\"e2e_avg_ms\":%.2f,\"e2e_max_ms\":%.2f,\"event_max_us\":%.1f},"
        "\"events\":%u,\"udp\":{\"sent\":%u,\"errors\":%u}}",
        u, ud, bound(),
        (unsigned)uxQueueMessagesWaiting(readingQueue), (unsigned)c.reading_peak, READING_QUEUE_LENGTH,
        (unsigned)c.reading_drops, (unsigned)uxQueueMessagesWaiting(netQueue), (unsigned)c.net_peak,
        NET_QUEUE_LENGTH, (unsigned)c.net_drops,
        (unsigned)c.polls[0], (unsigned)c.polls[1], (unsigned)c.polls[2],
        (unsigned)c.new_values[0], (unsigned)c.new_values[1], (unsigned)c.new_values[2],
        (unsigned)c.frames, (unsigned)c.frames_complete, (unsigned)c.frames_partial,
        (unsigned)c.keyframes, (unsigned long long)c.bytes_compressed, (unsigned)c.encode_errors,
        c.e2e_count ? c.e2e_sum_ns / 1e6 / c.e2e_count : 0.0, c.e2e_max_ns / 1e6,
        us(c.event_latency_max_ns), (unsigned)c.light_events, (unsigned)c.udp_sent, (unsigned)c.udp_errors);
    return n;
}

static void print_summary(void)
{
    TaskStats s[TASK_COUNT];
    Counters c = g_counters;
    double u, ud;
    int i;

    rt_snapshot(s);
    schedulability(s, &u, &ud);
    printf("\n===== SUMMARY =====\n%-16s %4s %5s %8s %6s %5s %4s %9s %9s %9s %10s\n", "task", "prio", "T(ms)",
           "D(us)", "jobs", "miss", "ovr", "Cavg(us)", "Cmax(us)", "Jmax(us)", "Rmax(us)");
    for (i = 0; i < TASK_COUNT; i++) {
        printf("%-16s %4u %5u %8u %6u %5u %4u %9.1f %9.1f %9.1f %10.1f\n", s[i].name, s[i].priority,
               (unsigned)s[i].period_ms, (unsigned)s[i].deadline_us, (unsigned)s[i].jobs,
               (unsigned)s[i].deadline_misses, (unsigned)s[i].overruns, avg_exec_us(&s[i]),
               us(s[i].exec_max_ns), us(s[i].jitter_max_ns), us(s[i].response_max_ns));
    }
    printf("U = %.5f, Ud = %.5f (bound %.4f) | new values T/H/L %u/%u/%u | frames %u (complete %u, partial %u)\n"
           "queue drops reading %u, net %u | light events %u | compressed %llu bytes\n",
           u, ud, bound(), (unsigned)c.new_values[0], (unsigned)c.new_values[1], (unsigned)c.new_values[2],
           (unsigned)c.frames, (unsigned)c.frames_complete, (unsigned)c.frames_partial,
           (unsigned)c.reading_drops, (unsigned)c.net_drops, (unsigned)c.light_events,
           (unsigned long long)c.bytes_compressed);
    fflush(stdout);
}

void health_task(void *argument)
{
    static char text[4096];
    static NetMessage msg;
    static uint64_t prev_cpu[TASK_COUNT];
    TickType_t last_wake = xTaskGetTickCount();
    TickType_t prev_tick = last_wake;
    uint64_t start = mono_ns(), prev = start;
    uint32_t seconds = 0;
    (void)argument;

    while (1) {
        JobTimer job;
        TaskStats s[TASK_COUNT];
        double cpu[TASK_COUNT], elapsed, tick_hz;
        uint64_t now;
        int len, i;

        if (xTaskDelayUntil(&last_wake, pdMS_TO_TICKS(HEALTH_PERIOD_MS)) == pdFALSE) {
            rt_overrun(TASK_HEALTH);
        }
        rt_job_start(&job, rt_tick_time_ns(last_wake));
        seconds++;

        /* real tick rate and CPU % of each task during the last second */
        now = mono_ns();
        elapsed = (double)(now - prev);
        tick_hz = (xTaskGetTickCount() - prev_tick) * 1e9 / elapsed;
        rt_snapshot(s);
        for (i = 0; i < TASK_COUNT; i++) {
            cpu[i] = 100.0 * (s[i].cpu_ns - prev_cpu[i]) / elapsed;
            prev_cpu[i] = s[i].cpu_ns;
        }
        prev = now;
        prev_tick = xTaskGetTickCount();

        len = build_report(text, sizeof(text), (now - start) / 1e9, tick_hz, cpu);
        if (len > 0 && len < (int)sizeof(msg.data)) {
            memcpy(msg.data, text, len);
            msg.length = (uint16_t)len;
            msg.port = TELEMETRY_PORT;
            msg.queued_ns = mono_ns();
            msg.oldest_read_ns = 0;
            post(netQueue, &msg, false, &g_counters.net_drops, &g_counters.net_peak);
        }

        printf("[HEALTH] %us | new T/H/L %u/%u/%u | frames %u (partial %u) | drops %u/%u | events %u\n",
               (unsigned)seconds, (unsigned)g_counters.new_values[0], (unsigned)g_counters.new_values[1],
               (unsigned)g_counters.new_values[2], (unsigned)g_counters.frames,
               (unsigned)g_counters.frames_partial, (unsigned)g_counters.reading_drops,
               (unsigned)g_counters.net_drops, (unsigned)g_counters.light_events);
        fflush(stdout);
        rt_job_end(TASK_HEALTH, &job);

        if (g_stop_requested || (g_duration_s > 0 && seconds >= g_duration_s)) {
            print_summary();
            exit(0);
        }
    }
}
