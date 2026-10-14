/* rt_stats.c - timing of every task (execution time, jitter, response time, misses) */
#include "rt_stats.h"
#include <string.h>
#include <time.h>

static TaskStats stats[TASK_COUNT];

#define TICK_HISTORY 256
static uint64_t tick_time[TICK_HISTORY];

static uint64_t clock_ns(clockid_t id)
{
    struct timespec ts;
    clock_gettime(id, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

uint64_t mono_ns(void)
{
    return clock_ns(CLOCK_MONOTONIC);
}

void rt_register(TaskId id, const char *name, uint8_t priority, uint32_t period_ms, uint32_t deadline_us)
{
    stats[id].name = name;
    stats[id].priority = priority;
    stats[id].period_ms = period_ms;
    stats[id].deadline_us = deadline_us;
}

/* The Linux tick is a bit slower than 1 ms, so we remember when each tick really
 * happened and measure jitter from the tick that released the job. */
void rt_tick_hook(void)
{
    tick_time[xTaskGetTickCountFromISR() % TICK_HISTORY] = mono_ns();
}

uint64_t rt_tick_time_ns(TickType_t tick)
{
    uint64_t t;
    taskENTER_CRITICAL();
    t = tick_time[tick % TICK_HISTORY];
    taskEXIT_CRITICAL();
    return t;
}

void rt_job_start(JobTimer *job, uint64_t release_ns)
{
    job->start_ns = mono_ns();
    job->start_cpu_ns = clock_ns(CLOCK_THREAD_CPUTIME_ID);
    if (release_ns == 0 || release_ns > job->start_ns) {
        release_ns = job->start_ns;
    }
    job->release_ns = release_ns;
}

void rt_job_end(TaskId id, JobTimer *job)
{
    uint64_t cpu = clock_ns(CLOCK_THREAD_CPUTIME_ID);
    uint64_t exec = cpu - job->start_cpu_ns;
    uint64_t jitter = job->start_ns - job->release_ns;
    uint64_t response = mono_ns() - job->release_ns;
    TaskStats *s = &stats[id];

    taskENTER_CRITICAL();
    s->jobs++;
    s->exec_sum_ns += exec;
    if (exec > s->exec_max_ns) s->exec_max_ns = exec;
    if (jitter > s->jitter_max_ns) s->jitter_max_ns = jitter;
    if (response > s->response_max_ns) s->response_max_ns = response;
    if (response > (uint64_t)s->deadline_us * 1000) s->deadline_misses++;
    s->cpu_ns = cpu;
    taskEXIT_CRITICAL();
}

void rt_overrun(TaskId id)
{
    taskENTER_CRITICAL();
    stats[id].overruns++;
    taskEXIT_CRITICAL();
}

void rt_snapshot(TaskStats out[TASK_COUNT])
{
    taskENTER_CRITICAL();
    memcpy(out, stats, sizeof(stats));
    taskEXIT_CRITICAL();
}
