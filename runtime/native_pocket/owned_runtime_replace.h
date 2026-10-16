#pragma once

#include <memory>
#include <stdexcept>
#include <utility>

// Only the serialized session owner mutates this cache. use_count is an
// ownership observation, not a substitute for synchronization. A borrower
// keeps its graph alive; an unborrowed old graph need not overlap the new
// allocation. Weights have their own shared lifetime and are not retired here.
template <class Runtime, class Factory>
void native_replace_owned_runtime(std::shared_ptr<Runtime>& cached, Factory&& make) {
    if (cached.use_count() == 1) cached.reset();
    try {
        auto replacement = std::forward<Factory>(make)();
        if (!replacement) throw std::runtime_error("replacement runtime is null");
        cached = std::move(replacement);
    } catch (...) {
        // Cache geometry may already name the requested graph. Never leave
        // the old graph under that new key after allocation fails.
        cached.reset();
        throw;
    }
}
