#pragma once

#include <array>
#include <mutex>
#include <cstdint>
#include <cmath>
#include <cstring>
#include <algorithm>
#include <vector>


class APU {
public:

    struct VoiceInput {
        int32_t pitch;
        int32_t pw;
        int32_t vol;
        int32_t wave;
        int32_t filter; // LP|BP|HP, 0 = dry
        int32_t cutoff; // 0..1023, linear 30 Hz .. 8 kHz
    };

    static constexpr int MIXRATE = 48000;
    static constexpr int VOICES  = 8;
    using VoiceInputs = std::array<VoiceInput, VOICES>;

    static void callback(void* u, uint8_t* stream, int bytes) {
        APU& apu = *(APU*)u;
        float* buffer = (float*)stream;
        int    length = bytes / sizeof(float);
        while (length > 0) {
            int& sample = apu.m_sample;
            if (sample == 0) apu.config_voices();
            int step = std::min(apu.m_frame_samples - sample, length);
            apu.mix(buffer, step);
            apu.capture(buffer, step);
            buffer += step;
            length -= step;
            sample += step;
            if (sample == apu.m_frame_samples) sample = 0;
        }
    }

    void next_frame(VoiceInputs const& voice_inputs) {
        std::lock_guard<std::mutex> lock(m_mtx);
        m_queue[m_write] = voice_inputs;
        m_write = (m_write + 1) % m_queue.size();
        if (m_count < m_queue.size()) m_count++;
        else m_read = (m_read + 1) % m_queue.size(); // full: drop oldest
    }

    void begin_capture() {
        std::lock_guard<std::mutex> lock(m_cap_mtx);
        m_capture.clear();
        m_capturing = true;
    }

    // Stops capture and returns mono float samples at MIXRATE.
    std::vector<float> end_capture() {
        std::lock_guard<std::mutex> lock(m_cap_mtx);
        m_capturing = false;
        return std::move(m_capture);
    }

private:
    enum { TRI = 1, SAW, PULSE, NOISE };
    enum { LP = 1, BP = 2, HP = 4 };

    void capture(float const* buf, size_t n) {
        std::lock_guard<std::mutex> lock(m_cap_mtx);
        if (!m_capturing) return;
        m_capture.insert(m_capture.end(), buf, buf + n);
    }

    void config_voices() {
        std::lock_guard<std::mutex> lock(m_mtx);
        if (m_count == 0) {
            m_frame_samples = MIXRATE / 180; // underrun: re-check sooner
            return;
        }
        // drain a little faster when queue is full, else nominal 60 Hz
        m_frame_samples = m_count >= m_queue.size() ? MIXRATE / 70 : MIXRATE / 60;
        VoiceInputs const& in = m_queue[m_read];
        m_read = (m_read + 1) % m_queue.size();
        m_count--;
        for (size_t i = 0; i < m_voices.size(); ++i) {
            Voice&            v = m_voices[i];
            VoiceInput const& m = in[i];
            int p = std::clamp(m.pitch, 0, 800);
            v.pitch = std::exp2f((p * (1.0f / 8.0f) - 57.0f) * (1.0f / 12.0f)) * (440.0f / MIXRATE);
            v.pw         = (m.pw % 1024) * (1.0f / 1024.0f);
            v.vol_target = std::clamp(m.vol * (1.0f / 1024.0f), 0.0f, 1.0f) * 0.125f;
            v.wave       = m.wave;
            v.filter     = m.filter & 7;
            float fc     = 30.0f + std::clamp(m.cutoff, 0, 1023) * (7970.0f / 1023.0f);
            v.cutoff     = 2.0f * std::sin(float(M_PI) * fc / MIXRATE);
            if (v.wave == NOISE) v.pitch *= 8.0f;
        }
    }

    void mix(float* out, size_t num_samples) {
        memset(out, 0, sizeof(float) * num_samples);
        for (Voice& v : m_voices) {
            for (size_t i = 0; i < num_samples; ++i) {
                v.phase += v.pitch;
                v.phase -= int(v.phase);
                float x = 0.0f;
                if (v.wave == SAW)   x = v.phase * 2.0f - 1.0f;
                if (v.wave == TRI)   x = 1.0f - 2.0f * std::abs(2.0f * v.phase - 1.0f);
                if (v.wave == PULSE) x = v.phase <= v.pw ? -1.0f : 1.0f;
                if (v.wave == NOISE) {
                    if (v.phase < v.pitch) {
                        uint32_t& s = v.shift;
                        s = (s << 1) | (((s >> 22) ^ (s >> 17)) & 1);
                        uint8_t n = ((s >> 22) & 1) << 7 |
                                    ((s >> 20) & 1) << 6 |
                                    ((s >> 16) & 1) << 5 |
                                    ((s >> 13) & 1) << 4 |
                                    ((s >> 11) & 1) << 3 |
                                    ((s >>  7) & 1) << 2 |
                                    ((s >>  4) & 1) << 1 |
                                    ((s >>  2) & 1) << 0;
                        v.noise = n * (1.0f / 128.0f) - 1.0f;
                    }
                    x = v.noise;
                }
                v.vol += (v.vol_target - v.vol) * 0.02f;
                x *= v.vol; // filter after volume, like the SID
                if (v.filter) {
                    // Chamberlin SVF, fixed resonance
                    float high = x - 0.6f * v.band - v.low;
                    v.band += v.cutoff * high;
                    v.low  += v.cutoff * v.band;
                    x = (v.filter & LP ? v.low  : 0.0f)
                      + (v.filter & BP ? v.band : 0.0f)
                      + (v.filter & HP ? high   : 0.0f);
                }
                out[i] += x;
            }
        }
    }

    struct Voice {
        float    pitch{};
        float    pw{};
        float    vol{};
        float    vol_target{};
        int      wave{};
        int      filter{}; // LP|BP|HP, 0 = dry
        float    cutoff{};
        float    low{};
        float    band{};
        float    phase{};
        float    noise{};
        uint32_t shift = 1234567;
    };
    std::array<Voice, VOICES>  m_voices{};
    std::array<VoiceInputs, 2> m_queue{};
    size_t                     m_read{};
    size_t                     m_write{};
    size_t                     m_count{};
    int                        m_frame_samples = MIXRATE / 60;
    int                        m_sample = 0; // position within the current frame, for callback()
    std::mutex                 m_mtx{};

    std::mutex                 m_cap_mtx{};
    bool                       m_capturing{};
    std::vector<float>         m_capture{};
};
