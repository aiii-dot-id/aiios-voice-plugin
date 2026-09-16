#pragma once
#include <algorithm>
#include <cstdint>
#include <mutex>
#include <stdexcept>

namespace aii::voice::pixel {
// Short control lock only: never held for model work, tensor I/O or callbacks.
// The inference owner retires before the next generation is admitted. The
// caller must also fence asynchronous delivery by the returned generation.
class GenerationGate {
    mutable std::mutex lock_;
    uint64_t last_=0, cancelled_=0;
    bool closed_=false;
    void check_locked(uint64_t id) const {
        if(closed_)throw std::runtime_error("native recognizer retired");
        if(!id || id<=cancelled_)throw std::runtime_error("recognition generation cancelled");
    }
public:
    void admit(uint64_t id) {
        std::lock_guard<std::mutex> guard(lock_);
        check_locked(id);
        if(id<=last_)throw std::runtime_error("recognition generation reused");
        last_=id;
    }
    void check(uint64_t id) const {
        std::lock_guard<std::mutex> guard(lock_);
        check_locked(id);
        if(id!=last_)throw std::runtime_error("recognition generation not current");
    }
    void cancel(uint64_t through) noexcept {
        std::lock_guard<std::mutex> guard(lock_);
        cancelled_=std::max(cancelled_,through);
    }
    void close() noexcept {
        std::lock_guard<std::mutex> guard(lock_);closed_=true;
    }
};
}
