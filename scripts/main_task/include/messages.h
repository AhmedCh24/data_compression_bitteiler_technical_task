#ifndef MESSAGES_H
#define MESSAGES_H
#include <stdbool.h>
#include <stdint.h>
#define COMPRESSED_PACKET_MAX 1200

typedef enum { SENSOR_TEMPERATURE = 0, SENSOR_HUMIDITY = 1, SENSOR_LIGHT = 2 } SensorKind;
typedef struct {
    uint64_t sample_id;
    uint64_t timestamp_ms;
    SensorKind sensor;
    bool valid;
    uint16_t value_tick;
    int16_t humidity_residual_1e4;
    uint64_t read_ns;     /* when the task read the value (for end-to-end latency) */
} SensorReading;
typedef struct {
    uint64_t sample_id;
    uint64_t timestamp_ms;
    uint16_t temperature_tick;
    uint16_t humidity_tick;
    int16_t humidity_residual_1e4;
    uint16_t light_tick;
} SensorFrame;
typedef struct { uint16_t length; uint8_t data[COMPRESSED_PACKET_MAX]; } CompressedPacket;

/* Light threshold crossing, sent by the light task to the light event task */
typedef struct {
    uint64_t sample_id;
    uint64_t detect_ns;   /* when the light task saw the crossing */
    float lux;
    bool rising;
} LightEvent;

/* One UDP datagram for the network task (compressed packet or JSON telemetry) */
#define NET_MAX_PAYLOAD 2048
typedef struct {
    uint16_t port;
    uint16_t length;
    uint64_t queued_ns;
    uint64_t oldest_read_ns;   /* 0 for telemetry */
    uint8_t data[NET_MAX_PAYLOAD];
} NetMessage;
#endif
