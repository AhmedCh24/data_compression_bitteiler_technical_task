#ifndef TASKS_H
#define TASKS_H
#include <stdbool.h>
#include <stdint.h>
#include "FreeRTOS.h"
#include "queue.h"
#include "task.h"
#include "messages.h"

extern QueueHandle_t readingQueue;     /* sensor tasks -> ingestion */
extern QueueHandle_t netQueue;         /* ingestion / event / health -> UDP task */
extern QueueHandle_t lightEventQueue;  /* light task -> light event task */

extern uint32_t g_duration_s;          /* 0 = run until Ctrl+C */
extern uint32_t g_speed;               /* replay speed, same as the feeder's --speed */
extern uint32_t g_period_ms;           /* sensor polling period = 10 s / speed */
extern volatile int g_stop_requested;

/* Counters shown on the dashboard */
typedef struct {
    uint32_t polls[3], new_values[3];        /* per sensor */
    uint32_t reading_drops, reading_peak;    /* readingQueue full / highest level */
    uint32_t net_drops, net_peak;
    uint32_t frames, frames_complete, frames_partial, encode_errors;
    uint64_t bytes_compressed;
    uint32_t keyframes;
    uint32_t udp_sent, udp_errors;
    uint32_t light_events;
    uint64_t event_latency_max_ns;
    uint64_t e2e_sum_ns, e2e_max_ns;
    uint32_t e2e_count;
} Counters;
extern Counters g_counters;

void temperature_acquisition_task(void *argument);
void humidity_acquisition_task(void *argument);
void light_acquisition_task(void *argument);
void light_event_task(void *argument);
void ingestion_task(void *argument);
void udp_task(void *argument);
void health_task(void *argument);

/* Put an item in a queue without waiting; counts drops and the highest level */
bool post(QueueHandle_t q, const void *item, bool to_front, uint32_t *drops, uint32_t *peak);
uint64_t mono_ns(void);
#endif
