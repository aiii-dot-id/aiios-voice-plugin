#pragma once
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace aii::multitalker {
class NemotronModel;
struct NemotronConfig {std::string model;int gpu=-1;bool share_dormant=true;bool refine_evidence=false;
  std::shared_ptr<NemotronModel> loaded{};};
class NemotronModel {
 public:
  explicit NemotronModel(const NemotronConfig&);
  ~NemotronModel();
  NemotronModel(const NemotronModel&)=delete;
  NemotronModel& operator=(const NemotronModel&)=delete;
  struct Impl;
  Impl& impl() const {return *p_;}
 private:
  std::unique_ptr<Impl> p_;
};
class Nemotron {
 public:
  explicit Nemotron(const NemotronConfig&);
  ~Nemotron();
  void reset(bool continue_capture);
  void push(const float*,size_t);
  void finish();
  uint64_t frames() const;
  uint64_t retained_frames() const;
  void discard_before(uint64_t frame);
  std::vector<float> range(uint64_t first,size_t count) const;
  std::unique_ptr<Nemotron> copy() const;
  std::vector<float> replay(const std::vector<float>& pcm,uint64_t frames,
                            const std::function<bool()>& cancelled,
                            const std::function<void()>& completed) const;
 private:
  struct Impl;
  explicit Nemotron(std::unique_ptr<Impl>);
  std::unique_ptr<Impl> p_;
};
}
