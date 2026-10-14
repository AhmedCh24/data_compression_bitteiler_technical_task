#ifndef SENSOR_IO_H
#define SENSOR_IO_H
#include <stdbool.h>
#include <stdint.h>
bool sensor_read_latest(const char *filename, uint64_t *sample_id, uint64_t *timestamp_ms, float *value);
bool light_threshold_crossed(float previous, float current, float threshold);
#endif
