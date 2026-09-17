#include "model_contract.h"
#include <iostream>

int main() {
  int failures = 0;
  auto expect = [&](bool ok, const char* why) { if (!ok) { ++failures; std::cerr << why << '\n'; } };
  auto refuse = [&](auto act, const char* why) {
    try { act(); expect(false, why); } catch (const std::invalid_argument&) {}
  };
  const auto& old = aii::uid::model_contract("c61bbdf12d5b69632b12776c23edc2e7155a9e0a028576eb41e509ca2bfb6d6e");
  const auto& candidate = aii::uid::model_contract("2575d15495d0e1cf17a2a12b0163dec0639db6218b3fe810e70369002d3898da");
  expect(old.bytes == 100865597 && candidate.bytes == 79158228, "checkpoint extent changed");
  expect(std::string(old.sha256) == "33af8affe6191b1ebd196d2b56e22c2934104cd2764abfdbdd954d3a934eb2a1", "existing model changed");
  expect(std::string(candidate.sha256) == "5b734353b4b410e222bbd124dd095537642237ad895727d18a3b9fee330262a8", "candidate model changed");
  for (const auto& model : aii::uid::model_contracts) {
    aii::uid::verify_model(model, model.bytes, model.sha256);
    expect(aii::uid::model_extent_supported(model.bytes), "declared checkpoint refused");
    refuse([&] { aii::uid::verify_model(model, model.bytes - 1, model.sha256); }, "short model accepted");
    refuse([&] { aii::uid::verify_model(model, model.bytes, std::string(64, '0')); }, "mutated model accepted");
  }
  refuse([&] { aii::uid::verify_model(old, candidate.bytes, candidate.sha256); }, "candidate silently used old enrollment space");
  refuse([&] { aii::uid::verify_model(candidate, old.bytes, old.sha256); }, "old checkpoint silently used candidate enrollment space");
  refuse([] { aii::uid::model_contract("unknown"); }, "undeclared numerical space accepted");
  expect(!aii::uid::model_extent_supported(0) && !aii::uid::model_extent_supported(100865598), "unbounded model admitted");
  const auto& n=aii::uid::ncnn_contract;
  aii::uid::verify_ncnn(candidate,n.graph_bytes,n.graph_sha256,n.weight_bytes,n.weight_sha256);
  refuse([&]{aii::uid::verify_ncnn(old,n.graph_bytes,n.graph_sha256,n.weight_bytes,n.weight_sha256);},"native representation reused old embedding space");
  refuse([&]{aii::uid::verify_ncnn(candidate,n.graph_bytes-1,n.graph_sha256,n.weight_bytes,n.weight_sha256);},"short native graph accepted");
  refuse([&]{aii::uid::verify_ncnn(candidate,n.graph_bytes,std::string(64,'0'),n.weight_bytes,n.weight_sha256);},"changed native graph accepted");
  refuse([&]{aii::uid::verify_ncnn(candidate,n.graph_bytes,n.graph_sha256,n.weight_bytes-1,n.weight_sha256);},"short native weights accepted");
  refuse([&]{aii::uid::verify_ncnn(candidate,n.graph_bytes,n.graph_sha256,n.weight_bytes,std::string(64,'0'));},"changed native weights accepted");
  if (!failures) std::cout << "model binding, exact bytes, and incompatible enrollment spaces proved\n";
  return failures ? 1 : 0;
}
