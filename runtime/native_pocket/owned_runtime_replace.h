#pragma once

#include <memory>
#include <stdexcept>
#include <utility>

template <class Runtime, class Factory>
void native_replace_owned_runtime(std::shared_ptr<Runtime>& cached, Factory&& make) {
    if (cached.use_count() == 1) cached.reset();
    try {
        auto replacement = std::forward<Factory>(make)();
        if (!replacement) throw std::runtime_error("replacement runtime is null");
        cached = std::move(replacement);
    } catch (...) {
        cached.reset();
        throw;
    }
}
