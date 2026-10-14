#ifndef COMPRESSION_H
#define COMPRESSION_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "messages.h"
/* 28-byte absolute keyframes and 6-byte fixed-width delta datagrams. */
bool compress_frames(const SensorFrame *frames, uint8_t count, CompressedPacket *packet);
bool decompress_packet(const uint8_t *data, size_t length, SensorFrame *frames,
                       uint8_t capacity, uint8_t *count);
#endif
