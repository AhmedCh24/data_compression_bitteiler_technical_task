#include "compression.h"
#include <limits.h>
#include <math.h>
#include <string.h>

#define MAGIC0 'F'
#define MAGIC1 'R'
#define VERSION 7u
#define TYPE_INIT 0u
#define TYPE_SAMPLE 1u
#define INIT_SIZE 28u
#define SAMPLE_SIZE 6u
#define KEYFRAME_INTERVAL 32u

typedef struct { bool initialized; uint8_t deltas_since_keyframe; SensorFrame previous; } CodecState;
static CodecState encoder, decoder;

static void put_u16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void put_u64(uint8_t *p,uint64_t v){for(unsigned i=0;i<8;i++)p[i]=(uint8_t)(v>>(8*i));}
static uint16_t get_u16(const uint8_t*p){return (uint16_t)(p[0]|((uint16_t)p[1]<<8));}
static uint64_t get_u64(const uint8_t*p){uint64_t v=0;for(unsigned i=0;i<8;i++)v|=(uint64_t)p[i]<<(8*i);return v;}
static bool fits_signed(int32_t v,unsigned n){return v>=-(1<<(n-1)) && v<=(1<<(n-1))-1;}
static uint32_t signed_bits(int32_t v,unsigned n){return (uint32_t)v & ((1u<<n)-1u);}
static int32_t sign_extend(uint32_t v,unsigned n){uint32_t sign=1u<<(n-1);return (v&sign)?(int32_t)(v-(1u<<n)):(int32_t)v;}
static void put40(uint8_t *p,uint64_t v){for(unsigned i=0;i<5;i++)p[i]=(uint8_t)(v>>(32-8*i));}
static uint64_t get40(const uint8_t*p){uint64_t v=0;for(unsigned i=0;i<5;i++)v=(v<<8)|p[i];return v;}

/* Each sample is one independent UDP datagram. The first sample and any
 * sample that cannot be represented as a delta is sent as an absolute keyframe.
 * Keyframe: 28 bytes (protocol header, exact sample ID/timestamp, sensor ticks).
 * Delta: 6 bytes (8-bit positive sample-ID delta + 40-bit sensor deltas).
 * 40-bit layout: dLight[13], dHumidityTick[11], humidity residual[5], dTemp[11]. */
static bool encode_keyframe(const SensorFrame *f, CompressedPacket *packet) {
    uint8_t *p = packet->data;
    memset(packet, 0, sizeof(*packet));
    p[0] = MAGIC0; p[1] = MAGIC1; p[2] = VERSION; p[3] = TYPE_INIT;
    put_u64(p + 4, f->sample_id);
    put_u64(p + 12, f->timestamp_ms);
    put_u16(p + 20, f->temperature_tick);
    put_u16(p + 22, f->humidity_tick);
    put_u16(p + 24, (uint16_t)f->humidity_residual_1e4);
    put_u16(p + 26, f->light_tick);
    packet->length = INIT_SIZE;
    encoder.previous = *f;
    encoder.initialized = true;
    encoder.deltas_since_keyframe = 0;
    return true;
}

bool compress_frames(const SensorFrame *frames, uint8_t count, CompressedPacket *packet) {
    if (!frames || !packet || count != 1) return false;
    const SensorFrame *f = &frames[0];
    if (!encoder.initialized) return encode_keyframe(f, packet);

    /* IDs can have gaps or move backwards in timestamp-sorted source data.
     * Re-keyframe rather than reject a valid sample or silently corrupt its ID. */
    if (encoder.deltas_since_keyframe >= KEYFRAME_INTERVAL - 1u ||
        f->sample_id <= encoder.previous.sample_id ||
        f->sample_id - encoder.previous.sample_id > 255u) {
        return encode_keyframe(f, packet);
    }

    int32_t dt = (int32_t)f->temperature_tick - (int32_t)encoder.previous.temperature_tick;
    int32_t dh = (int32_t)f->humidity_tick - (int32_t)encoder.previous.humidity_tick;
    int32_t dl = (int32_t)f->light_tick - (int32_t)encoder.previous.light_tick;
    /* The 5-bit residual is quantized to 0.1 percentage-point units. */
    int32_t hr = (int32_t)lround((double)f->humidity_residual_1e4 / 1000.0);

    /* A keyframe preserves exact sensor ticks and the unquantized humidity
     * residual whenever a delta would overflow its fixed-width field. */
    if (!fits_signed(dt, 11) || !fits_signed(dh, 11) ||
        !fits_signed(hr, 5) || !fits_signed(dl, 13)) {
        return encode_keyframe(f, packet);
    }

    uint64_t bits = ((uint64_t)signed_bits(dl, 13) << 27) |
                    ((uint64_t)signed_bits(dh, 11) << 16) |
                    ((uint64_t)signed_bits(hr, 5) << 11) |
                    signed_bits(dt, 11);
    memset(packet, 0, sizeof(*packet));
    packet->data[0] = (uint8_t)(f->sample_id - encoder.previous.sample_id);
    put40(packet->data + 1, bits);
    packet->length = SAMPLE_SIZE;
    encoder.previous = *f;
    encoder.deltas_since_keyframe++;
    return true;
}

bool decompress_packet(const uint8_t *data,size_t length,SensorFrame *frames,uint8_t capacity,uint8_t *count){
    if(!data||!frames||!count||capacity<1)return false;
    if(length==28&&data[0]==MAGIC0&&data[1]==MAGIC1&&data[2]==VERSION&&data[3]==TYPE_INIT){
        SensorFrame f={0};f.sample_id=get_u64(data+4);f.timestamp_ms=get_u64(data+12);
        f.temperature_tick=get_u16(data+20);f.humidity_tick=get_u16(data+22);
        f.humidity_residual_1e4=(int16_t)get_u16(data+24);f.light_tick=get_u16(data+26);
        decoder.previous=f;decoder.initialized=true;frames[0]=f;*count=1;return true;
    }
    if(length!=SAMPLE_SIZE||!decoder.initialized)return false;
    uint64_t b=get40(data+1);int32_t dl=sign_extend((uint32_t)(b>>27),13);
    int32_t dh=sign_extend((uint32_t)(b>>16)&0x7ffu,11);
    int32_t hr=sign_extend((uint32_t)(b>>11)&0x1fu,5);
    int32_t dt=sign_extend((uint32_t)b&0x7ffu,11);
    SensorFrame f=decoder.previous;
    if (UINT64_MAX - f.sample_id < data[0]) return false;
    f.sample_id += data[0];
    int32_t t=(int32_t)f.temperature_tick+dt,h=(int32_t)f.humidity_tick+dh,l=(int32_t)f.light_tick+dl;
    int32_t residual=hr*1000;
    if(t<0||t>UINT16_MAX||h<0||h>UINT16_MAX||l<0||l>UINT16_MAX||residual<INT16_MIN||residual>INT16_MAX)return false;
    f.temperature_tick=(uint16_t)t;f.humidity_tick=(uint16_t)h;f.light_tick=(uint16_t)l;f.humidity_residual_1e4=(int16_t)residual;
    /* Per-sample timestamps are supplied by the raw stream/dashboard matching. */
    decoder.previous=f;frames[0]=f;*count=1;return true;
}
