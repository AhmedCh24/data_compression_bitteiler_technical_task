#include "sensor_io.h"
#include <stdio.h>
#include <inttypes.h>

bool sensor_read_latest(const char *filename, uint64_t *sample_id,
                        uint64_t *timestamp_ms, float *value)
{
    if (!filename || !sample_id || !timestamp_ms || !value) return false;
    FILE *file = fopen(filename, "r");
    if (!file) return false;
    char line[256];
    if (!fgets(line, sizeof(line), file)) {
        fclose(file);
        return false;
    }
    uint64_t id, timestamp;
    float measurement;
    int result = sscanf(line, "%" SCNu64 ",%" SCNu64 ",%f",
                        &id, &timestamp, &measurement);
    fclose(file);
    if (result != 3) return false;
    *sample_id = id;
    *timestamp_ms = timestamp;
    *value = measurement;
    return true;
}

bool light_threshold_crossed(float previous, float current, float threshold)
{
    if (previous < threshold && current >= threshold) return true;
    if (previous >= threshold && current < threshold) return true;
    return false;
}
