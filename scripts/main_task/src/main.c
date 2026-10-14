/*
 * main.c - creates the queues and tasks and starts FreeRTOS
 *
 *   ./build/sensor_monitor [--speed N] [--duration S]
 *   --speed must be the same as the feeder's (default 30).
 *
 * Priorities (see config.h and SCHEDULING_DESIGN.md):
 *   LightEvent 5 > sensors 4 (same period -> same priority) > Ingestion 3 > UDP 2 > Health 1
 */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "config.h"
#include "rt_stats.h"
#include "tasks.h"

uint32_t g_duration_s = 0;
uint32_t g_speed = DEFAULT_REPLAY_SPEED;
uint32_t g_period_ms = 0;
volatile int g_stop_requested = 0;

/* Ctrl+C only sets a flag: the health task stops the program */
static void on_ctrl_c(int s) { (void)s; g_stop_requested = 1; }

void vApplicationTickHook(void) { rt_tick_hook(); }
void vApplicationMallocFailedHook(void) { fprintf(stderr, "out of heap\n"); abort(); }

bool post(QueueHandle_t q, const void *item, bool to_front, uint32_t *drops, uint32_t *peak)
{
    BaseType_t ok = to_front ? xQueueSendToFront(q, item, 0) : xQueueSend(q, item, 0);
    UBaseType_t level = uxQueueMessagesWaiting(q);
    taskENTER_CRITICAL();
    if (ok != pdPASS) (*drops)++;
    if (level > *peak) *peak = (uint32_t)level;
    taskEXIT_CRITICAL();
    return ok == pdPASS;
}

static void create(TaskFunction_t fn, const char *name, int stack, int prio, TaskId id,
                   uint32_t period_ms, uint32_t deadline_us)
{
    if (xTaskCreate(fn, name, stack, NULL, prio, NULL) != pdPASS) {
        fprintf(stderr, "cannot create %s\n", name);
        exit(1);
    }
    rt_register(id, name, (uint8_t)prio, period_ms, deadline_us);
}

int main(int argc, char **argv)
{
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--speed") == 0) {
            g_speed = (uint32_t)atoi(argv[i + 1]);
        } else if (strcmp(argv[i], "--duration") == 0) {
            g_duration_s = (uint32_t)atoi(argv[i + 1]);
        }
    }
    if (g_speed == 0) {
        g_speed = 1;
    }
    /* poll every 10 s of dataset time: real-time period = 10 s / speed */
    g_period_ms = (DATASET_POLL_INTERVAL_MS + g_speed / 2) / g_speed;
    if (g_period_ms < MIN_PERIOD_MS) {
        fprintf(stderr, "speed %u too high: period would be below %d ms\n", (unsigned)g_speed, MIN_PERIOD_MS);
        return 1;
    }
    signal(SIGINT, on_ctrl_c);

    readingQueue = xQueueCreate(READING_QUEUE_LENGTH, sizeof(SensorReading));
    netQueue = xQueueCreate(NET_QUEUE_LENGTH, sizeof(NetMessage));
    lightEventQueue = xQueueCreate(LIGHT_EVENT_QUEUE_LENGTH, sizeof(LightEvent));
    if (!readingQueue || !netQueue || !lightEventQueue) {
        fprintf(stderr, "cannot create the queues\n");
        return 1;
    }

    /* function, name, stack, priority, id, period (ms), deadline (us) */
    create(light_event_task, "LightEvent", 2048, PRIO_LIGHT_EVENT, TASK_LIGHT_EVENT, 0, LIGHT_EVENT_DEADLINE_US);
    /* sensors: deadline = period; ingestion (soft): a reading is processed within one period */
    create(light_acquisition_task, "LightTask", 2048, PRIO_SENSORS, TASK_LIGHT, g_period_ms, g_period_ms * 1000);
    create(temperature_acquisition_task, "TemperatureTask", 2048, PRIO_SENSORS, TASK_TEMPERATURE,
           g_period_ms, g_period_ms * 1000);
    create(humidity_acquisition_task, "HumidityTask", 2048, PRIO_SENSORS, TASK_HUMIDITY,
           g_period_ms, g_period_ms * 1000);
    create(ingestion_task, "IngestionTask", 4096, PRIO_INGESTION, TASK_INGESTION, 0, g_period_ms * 1000);
    create(udp_task, "UDPTask", 4096, PRIO_UDP, TASK_UDP, 0, UDP_DEADLINE_MS * 1000);
    create(health_task, "HealthTask", 4096, PRIO_HEALTH, TASK_HEALTH, HEALTH_PERIOD_MS, HEALTH_PERIOD_MS * 1000);

    printf("[MAIN] speed %ux -> polling period %u ms (10 s of dataset time) | priorities: LightEvent 5, sensors 4 (light, temperature, humidity), "
           "Ingestion 3, UDP 2, Health 1\n", (unsigned)g_speed, (unsigned)g_period_ms);
    fflush(stdout);
    vTaskStartScheduler();
    return 0;
}
