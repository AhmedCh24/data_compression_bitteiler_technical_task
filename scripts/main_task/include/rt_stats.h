#ifndef RT_STATS_H
#define RT_STATS_H
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"

typedef enum {
    TASK_LIGHT_EVENT, TASK_LIGHT, TASK_TEMPERATURE, TASK_HUMIDITY,
    TASK_INGESTION, TASK_UDP, TASK_HEALTH, TASK_COUNT
} TaskId;

/* Timing of one task. One job = one run of its loop.
 *   exec = CPU time of the job, jitter = start - release, response = end - release */
typedef struct {
    const char *name;
    uint8_t priority;
    uint32_t period_ms;      /* 0 = event driven */
    uint32_t deadline_us;
    uint32_t jobs, deadline_misses, overruns;
    uint64_t exec_sum_ns, exec_max_ns, jitter_max_ns, response_max_ns, cpu_ns;
} TaskStats;

typedef struct { uint64_t release_ns, start_ns, start_cpu_ns; } JobTimer;

void rt_register(TaskId id, const char *name, uint8_t priority, uint32_t period_ms, uint32_t deadline_us);
void rt_tick_hook(void);                     /* called on every tick: remembers its time */
uint64_t rt_tick_time_ns(TickType_t tick);   /* release time of a periodic job */
void rt_job_start(JobTimer *job, uint64_t release_ns);
void rt_job_end(TaskId id, JobTimer *job);
void rt_overrun(TaskId id);
void rt_snapshot(TaskStats out[TASK_COUNT]);
#endif
