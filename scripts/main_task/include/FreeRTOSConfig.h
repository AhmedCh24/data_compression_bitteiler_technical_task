#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H
#define configUSE_PREEMPTION 1
#define configUSE_TIME_SLICING 1
#define configCPU_CLOCK_HZ 1000000UL
#define configTICK_RATE_HZ 1000
#define configMAX_PRIORITIES 8
#define configMINIMAL_STACK_SIZE 2048
#define configTOTAL_HEAP_SIZE (256 * 1024)
#define configMAX_TASK_NAME_LEN 32
#define configUSE_16_BIT_TICKS 0
#define configIDLE_SHOULD_YIELD 1
#define configUSE_MUTEXES 1
#define configUSE_RECURSIVE_MUTEXES 1
#define configUSE_COUNTING_SEMAPHORES 1
#define configUSE_QUEUE_SETS 0
#define configUSE_TASK_NOTIFICATIONS 1
#define configTASK_NOTIFICATION_ARRAY_ENTRIES 1
#define configUSE_TIMERS 0
#define configTIMER_TASK_PRIORITY 2
#define configTIMER_TASK_STACK_DEPTH 2048
#define configTIMER_QUEUE_LENGTH 10
#define configUSE_TRACE_FACILITY 1
#define configCHECK_FOR_STACK_OVERFLOW 0
#define configUSE_MALLOC_FAILED_HOOK 1
#define configUSE_IDLE_HOOK 0
#define configUSE_TICK_HOOK 1
#define configGENERATE_RUN_TIME_STATS 0
#define configSUPPORT_DYNAMIC_ALLOCATION 1
#define configSUPPORT_STATIC_ALLOCATION 0
#define INCLUDE_vTaskDelay 1
#define INCLUDE_vTaskDelayUntil 1
#define INCLUDE_vTaskDelete 1
#define INCLUDE_xTaskGetSchedulerState 1
#define INCLUDE_xTaskGetCurrentTaskHandle 1
#define INCLUDE_xTaskGetIdleTaskHandle 1
#define INCLUDE_uxTaskGetStackHighWaterMark 1
#define INCLUDE_vTaskPrioritySet 1
#define INCLUDE_uxTaskPriorityGet 1
#define INCLUDE_xTaskGetHandle 1
#define INCLUDE_xTaskAbortDelay 1
#include <stdio.h>
#include <stdlib.h>
#define configASSERT(x) do { if ((x) == 0) { fprintf(stderr, "FreeRTOS assertion failed: %s:%d\n", __FILE__, __LINE__); abort(); } } while (0)
#endif
