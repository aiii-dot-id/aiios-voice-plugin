#pragma once
// Private model-construction owner. One explicitly launched task, no detached
// work. Destruction waits for construction and destroys an unclaimed result.
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
