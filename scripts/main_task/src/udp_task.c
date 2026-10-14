/* udp_task.c - the only task that uses the socket (compressed packets + telemetry) */
#include "tasks.h"
#include "config.h"
#include "rt_stats.h"
#include <arpa/inet.h>
#include <string.h>
#include <sys/socket.h>

QueueHandle_t netQueue = NULL;

void udp_task(void *argument)
{
    static NetMessage msg;
    struct sockaddr_in destination;
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    (void)argument;

    memset(&destination, 0, sizeof(destination));
    destination.sin_family = AF_INET;
    inet_pton(AF_INET, UDP_SERVER_IP, &destination.sin_addr);

    while (1) {
        JobTimer job;

        xQueueReceive(netQueue, &msg, portMAX_DELAY);
        rt_job_start(&job, msg.queued_ns);
        destination.sin_port = htons(msg.port);

        /* MSG_DONTWAIT: never block; a failed send is counted */
        if (sendto(fd, msg.data, msg.length, MSG_DONTWAIT, (struct sockaddr *)&destination,
                   sizeof(destination)) == msg.length) {
            g_counters.udp_sent++;
            if (msg.oldest_read_ns != 0) {   /* compressed packet: read -> sent latency */
                uint64_t latency = mono_ns() - msg.oldest_read_ns;
                g_counters.e2e_sum_ns += latency;
                g_counters.e2e_count++;
                if (latency > g_counters.e2e_max_ns) g_counters.e2e_max_ns = latency;
            }
        } else {
            g_counters.udp_errors++;
        }
        rt_job_end(TASK_UDP, &job);
    }
}
