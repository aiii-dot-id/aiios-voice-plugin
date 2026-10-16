#include "confirmed_acts.h"
#include <iostream>
#include <stdexcept>

static void need(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
template<class F> static void refused(F fn) {
  bool caught=false;
  try { fn(); } catch (const std::invalid_argument&) { caught=true; }
  need(caught,"invalid or replayed operator act accepted");
}
int main() {
  aii::voice::ConfirmedActs acts;
  for (int n=0;n<2048;++n) acts.consume("auto");
  acts.check("manual"); acts.check("manual"); // validation is not consumption
  acts.consume("manual");
  refused([&]{acts.check("manual");});
  refused([&]{acts.consume("manual");});
  refused([&]{acts.consume("");});
  refused([&]{acts.consume(std::string(129,'x'));});
  for (int n=1;n<1024;++n) acts.consume("manual-"+std::to_string(n));
  refused([&]{acts.consume("overflow");});
  acts.consume("auto"); // standing confirmation is not replay storage
  std::cout << "standing confirmations repeat; one-use acts stay replay-guarded and bounded PASS\n";
}
