#pragma once
#include <future>
#include <memory>
#include <stdexcept>
#include <utility>

namespace aii::voice {
template<class T> class PendingModel {
  std::future<std::unique_ptr<T>> pending_;
  std::unique_ptr<T> value_;
public:
  template<class Factory> explicit PendingModel(Factory factory)
      : pending_(std::async(std::launch::async, std::move(factory))) {}
  PendingModel(const PendingModel&) = delete;
  PendingModel& operator=(const PendingModel&) = delete;
  T& get() {
    if (pending_.valid()) value_ = pending_.get();
    if (!value_) throw std::runtime_error("model construction did not produce an owner");
    return *value_;
  }
};
}
