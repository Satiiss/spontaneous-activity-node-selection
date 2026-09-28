// Passive spike-stream reader. No initialization, routing or stimulation calls.
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <iostream>
#include <fstream>
#include <map>
#include <vector>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#ifdef __linux__
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#endif
#ifdef WITH_MAXLAB
#include <maxlab/maxlab.h>
#endif

using Clock = std::chrono::steady_clock;
volatile std::sig_atomic_t stopped = 0;
void stop(int) { stopped = 1; }

// Bounded pipe backpressure: fail instead of silently discarding data.
void emit(const std::string& text) {
#ifdef __linux__
    size_t offset = 0;
    const auto deadline = Clock::now() + std::chrono::seconds(1);
    while (offset < text.size()) {
        if (Clock::now() >= deadline) throw std::runtime_error("output stalled");
        auto n = ::write(STDOUT_FILENO, text.data() + offset, text.size() - offset);
        if (n > 0) { offset += static_cast<size_t>(n); continue; }
        if (n < 0 && errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)
            throw std::runtime_error("output pipe failed");
        pollfd p{STDOUT_FILENO, POLLOUT, 0};
        ::poll(&p, 1, 10);
    }
#else
    std::cout << text << std::flush;
    if (!std::cout) throw std::runtime_error("output pipe failed");
#endif
}

uint64_t number(const std::string& s) {
    if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("expected unsigned integer");
    return std::stoull(s);
}

int main(int argc, char** argv) {
    bool opened = false;
    int result = 0;
    try {
        std::string mode = "mock", filter = "iir", mock_spikes;
        uint64_t rate = 20000, well = 0, frames = 20000;
        bool rate_set = false;
        for (int i = 1; i < argc; ++i) {
            std::string key = argv[i];
            if (key == "--help") {
                std::cerr << "--mode mock|maxlab --sample-rate HZ --well N --filter iir|fir --frames N (0=continuous) --mock-spikes FILE\n";
                return 0;
            }
            if (++i == argc) throw std::runtime_error("missing option value");
            std::string value = argv[i];
            if (key == "--mode") mode = value;
            else if (key == "--filter") filter = value;
            else if (key == "--sample-rate") { rate = number(value); rate_set = true; }
            else if (key == "--well") well = number(value);
            else if (key == "--frames") frames = number(value);
            else if (key == "--mock-spikes") mock_spikes = value;
            else throw std::runtime_error("unknown option: " + key);
        }
        if ((mode != "mock" && mode != "maxlab") || (filter != "iir" && filter != "fir") ||
            !rate || rate > 1000000 || well > 255)
            throw std::runtime_error("invalid configuration");
        if (mode == "maxlab" && !rate_set)
            throw std::runtime_error("maxlab requires explicitly verified --sample-rate");
        if (mode != "mock" && !mock_spikes.empty())
            throw std::runtime_error("mock spikes are forbidden with maxlab input");
        std::map<uint64_t, std::vector<std::pair<unsigned, float>>> fixture;
        if (!mock_spikes.empty()) {
            std::ifstream input(mock_spikes);
            if (!input) throw std::runtime_error("cannot open mock spike fixture");
            std::string line;
            size_t entries = 0;
            while (std::getline(input, line)) {
                std::istringstream row(line);
                std::string f, c, extra;
                float amplitude;
                if (!(row >> f >> c >> amplitude) || (row >> extra))
                    throw std::runtime_error("mock fixture must contain: frame channel amplitude");
                auto channel = number(c), timestamp = number(f);
                if (channel >= 1024 || !std::isfinite(amplitude) || ++entries > 100000 ||
                    (frames && timestamp >= frames)) throw std::runtime_error("invalid mock spike fixture");
                auto& events = fixture[timestamp];
                if (events.size() >= 1024) throw std::runtime_error("too many mock spikes per frame");
                events.emplace_back(static_cast<unsigned>(channel), amplitude);
            }
        }
        std::signal(SIGINT, stop);
        std::signal(SIGTERM, stop);
#ifdef __linux__
        std::signal(SIGPIPE, SIG_IGN);
        int flags = fcntl(STDOUT_FILENO, F_GETFL);
        if (flags < 0 || fcntl(STDOUT_FILENO, F_SETFL, flags | O_NONBLOCK) < 0)
            throw std::runtime_error("cannot configure output");
#endif
        if (mode == "maxlab") {
#ifdef WITH_MAXLAB
            maxlab::checkVersions();
            auto status = maxlab::DataStreamerFiltered_open(filter == "iir" ? maxlab::FilterType::IIR : maxlab::FilterType::FIR);
            if (status != maxlab::MAXLAB_OK) throw std::runtime_error(maxlab::statusToText(status));
            opened = true;
#else
            throw std::runtime_error("rebuild with WITH_MAXLAB=ON for hardware input");
#endif
        }
        emit("{\"type\":\"hello\",\"version\":1,\"mode\":\"" + mode +
             "\",\"filter\":\"" + filter + "\",\"sample_rate\":" + std::to_string(rate) +
             ",\"well\":" + std::to_string(well) + "}\n");
        uint64_t count = 0, previous = 0, first = 0, batch_count = 0, spike_count = 0;
        std::ostringstream spikes;
        auto last_frame = Clock::now();
        const auto start = last_frame;
        auto flush = [&]() {
            if (!batch_count) return;
            emit("{\"type\":\"batch\",\"first\":" + std::to_string(first) +
                 ",\"last\":" + std::to_string(previous) + ",\"spikes\":[" + spikes.str() + "]}\n");
            batch_count = 0; spike_count = 0; spikes.str(""); spikes.clear();
        };
        auto add = [&](uint64_t frame, unsigned channel, float amplitude) {
            if (channel >= 1024 || !std::isfinite(amplitude)) throw std::runtime_error("invalid spike");
            if (spike_count++) spikes << ',';
            spikes << '[' << frame << ',' << channel << ',' << amplitude << ']';
        };
        while (!stopped && (!frames || count < frames)) {
            uint64_t frame = count;
#ifdef WITH_MAXLAB
            maxlab::FilteredFrameData data{};
            if (opened) {
                auto status = maxlab::DataStreamerFiltered_receiveNextFrame(&data);
                if (status == maxlab::MAXLAB_NO_FRAME) {
                    if (Clock::now() - last_frame > std::chrono::seconds(2)) throw std::runtime_error("stream timeout");
                    continue;
                }
                if (status != maxlab::MAXLAB_OK) throw std::runtime_error(maxlab::statusToText(status));
                if (data.frameInfo.well_id != well) {
                    if (Clock::now() - last_frame > std::chrono::seconds(2)) throw std::runtime_error("target well timeout");
                    continue;
                }
                if (data.frameInfo.corrupted) throw std::runtime_error("corrupted frame");
                frame = data.frameInfo.frame_number;
            }
#endif
            if (count && (frame <= previous || frame - previous != 1))
                throw std::runtime_error("frame discontinuity: restart and invalidate active trial");
            if (!batch_count) first = frame;
            previous = frame; ++batch_count; ++count; last_frame = Clock::now();
#ifdef WITH_MAXLAB
            if (opened) {
                if (data.spikeCount > 1024 || (data.spikeCount && !data.spikeEvents))
                    throw std::runtime_error("invalid SDK spike buffer");
                for (uint64_t i = 0; i < data.spikeCount; ++i) {
                    const auto& spike = data.spikeEvents[i];
                    // Conservative until onsite SDK timestamp semantics have been verified.
                    if (spike.wellId != well || spike.frameNo != frame)
                        throw std::runtime_error("spike/frame timestamp or well mismatch");
                    add(spike.frameNo, spike.channel, spike.amp);
                }
            } else
#endif
            {
                if (!mock_spikes.empty()) {
                    auto found = fixture.find(frame);
                    if (found != fixture.end())
                        for (const auto& spike : found->second) add(frame, spike.first, spike.second);
                } else if (frame % 100 == 0) add(frame, 0, -42.0f);
            }
            if (batch_count >= 200 || spike_count >= 4096) {
                flush();
                if (mode == "mock") std::this_thread::sleep_until(start + std::chrono::microseconds(count * 1000000 / rate));
            }
        }
        flush();
        emit("{\"type\":\"end\",\"frames\":" + std::to_string(count) + "}\n");
    } catch (const std::exception& e) {
        std::cerr << "mea_reader: " << e.what() << '\n';
        result = 1;
    }
#ifdef WITH_MAXLAB
    if (opened) {
        auto status = maxlab::DataStreamerFiltered_close();
        if (status != maxlab::MAXLAB_OK) {
            std::cerr << "stream close failed; check mxwserver before reuse: " << maxlab::statusToText(status) << '\n';
            result = 1;
        }
    }
#else
    (void)opened;
#endif
    return result;
}
