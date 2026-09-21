#pragma once
#include "speaker_observation.h"
#include "speaker_limits.h"
#include <deque>
#include <map>
#include <vector>

namespace aii::voice::wire {
// Single control-thread owner. This binds decisions; it does not detect overlap
// or manufacture clean acoustic evidence. A capture stream is NOT a speaker.
struct FinalKey {
  std::string session, track;
  uint64_t sequence = 0, start = 0, end = 0;
  bool operator==(const FinalKey& b) const {
    return session==b.session && track==b.track && sequence==b.sequence &&
           start==b.start && end==b.end;
  }
};
struct CleanEvidence {
  FinalKey final;
  uint64_t start = 0, end = 0;
  std::string enrollment_revision, policy_sha256, embedding_binding;
};
class Attributions {
 public:
  static constexpr size_t capacity = 128, pending_capacity = speaker_pending_capacity;
  static constexpr uint64_t timeout_ms = 15000;
  void begin(const std::string& session) {
    require(!session.empty() && session.size()<=128,"invalid attribution session");
    require(rows_.empty() || pending()==0,"unresolved attribution at session replacement");
    session_=session; rows_.clear(); order_.clear(); last_sequence_=0;
  }
  Json add(uint64_t core_sequence, const FinalKey& key, uint64_t now_ms,
           bool matcher_available) {
    require(core_sequence && key.session==session_ && key.sequence>last_sequence_ &&
            key.sequence<=9007199254740991ULL && key.start<key.end &&
            key.end<=9007199254740991ULL && key.track.size()<=96,
            "invalid attribution final binding");
    require(!rows_.count(core_sequence),"replayed attribution core sequence");
    size_t outstanding=0;for(const auto& item:rows_)outstanding+=item.second.awaiting;
    require(!matcher_available || outstanding<pending_capacity,"pending attribution bound exceeded");
    if(rows_.size()==capacity) {
      auto old=order_.begin();
      while(old!=order_.end() && rows_.at(*old).awaiting)++old;
      require(old!=order_.end(),"attribution history full");
      rows_.erase(*old); order_.erase(old);
    }
    auto initial=state(matcher_available?"pending":"uncertain",
                       matcher_available?"speaker_match_pending":"speaker_identification_unavailable",0);
    rows_.emplace(core_sequence,Row{key,now_ms,matcher_available,encode(initial),{},false,matcher_available});
    order_.push_back(core_sequence);last_sequence_=key.sequence;
    return initial;
  }
  // Returns no event for an identical duplicate or for a late verdict after a
  // locally declared timeout/cancellation. Conflicting evidence cannot rewrite
  // a settled decision. Lookup is before mutation, including session checks.
  Json resolve(uint64_t core_sequence, const FinalKey& key, Json detail,
               const CleanEvidence* clean=nullptr) {
    auto& row=lookup(core_sequence,key);
    auto observed=speaker_observation(std::move(detail),key.sequence);
    const auto decision=str(field(observed.get(),"decision"));
    if(decision=="known" || decision=="unknown") {
      if(!clean) {
        auto unavailable=object();
        put(unavailable,"outcome",string("unavailable"));
        put(unavailable,"reason",string("speaker_track_unverified"));
        // A pooled match's label/score must not leak as candidate identity.
        observed=speaker_observation(std::move(unavailable),key.sequence);
      } else {
        require(clean->final==key && !key.track.empty() && clean->start>=key.start &&
                clean->start<clean->end && clean->end<=key.end &&
                !clean->enrollment_revision.empty() && clean->enrollment_revision.size()<=64 &&
                digest(clean->policy_sha256) && digest(clean->embedding_binding),
                "speaker evidence belongs to a different or unqualified segment");
        auto scope=object();
        put(scope,"start_sample",number(clean->start));put(scope,"end_sample",number(clean->end));
        put(scope,"enrollment_revision",string(clean->enrollment_revision));
        put(scope,"policy_sha256",string(clean->policy_sha256));
        put(scope,"embedding_binding",string(clean->embedding_binding));
        put(observed,"evidence_scope",std::move(scope));
      }
    } else require(!clean,"unresolved decision must not claim match evidence");
    // Consumer evidence is explicitly projected below. Raw matcher diagnostics
    // can contain rejected candidates, labels or private enrollment details.
    cJSON_DeleteItemFromObject(observed.get(),"native_evidence");
    put(observed,"revision",number(1));bind(observed,key);
    const auto canonical=encode(observed);
    if(!row.pending) {
      if(row.locally_retired) {row.awaiting=false;return null();}
      if(row.resolution==canonical)return null();
      throw Refused("conflicting attribution after terminal resolution");
    }
    row.pending=false;row.awaiting=false;row.resolution=canonical;
    row.state=encode(summary(observed.get()));
    return observed;
  }
  FinalKey key(uint64_t core_sequence, const std::string& session,
               uint64_t start, uint64_t end) const {
    const auto it=rows_.find(core_sequence);
    require(it!=rows_.end() && session==session_ && it->second.key.session==session &&
            it->second.key.start==start && it->second.key.end==end,
            "speaker observation changed session or sample span");
    return it->second.key;
  }
  std::vector<Json> expire(uint64_t now_ms) {
    return retire("speaker_match_timeout",[&](const Row& r) {
      require(now_ms>=r.admitted_ms,"attribution clock moved backwards");
      return now_ms-r.admitted_ms>=timeout_ms;
    });
  }
  std::vector<Json> end(const std::string& reason) {
    require(reason=="session_aborted" || reason=="session_failed" ||
            reason=="speaker_result_missing","invalid attribution retirement reason");
    return retire(reason,[](const Row&){return true;});
  }
  Json snapshot() const {
    auto out=own(cJSON_CreateArray());
    for(const auto id:order_) {
      const auto& row=rows_.at(id);auto item=parse(row.state);bind(item,row.key);
      put(item,"refers_to",number(row.key.sequence));
      require(cJSON_AddItemToArray(out.get(),item.get()),"attribution snapshot allocation");
      item.release();
    }
    return out;
  }
  size_t size() const {return rows_.size();}
  size_t pending() const {
    size_t n=0;for(const auto& item:rows_)n+=item.second.pending;return n;
  }
 private:
  struct Row {
    FinalKey key;uint64_t admitted_ms;bool pending;std::string state,resolution;
    bool locally_retired=false,awaiting=false;
  };
  std::string session_;uint64_t last_sequence_=0;
  std::map<uint64_t,Row> rows_;std::deque<uint64_t> order_;
  static bool digest(const std::string& s) {
    return s.size()==64 && s.find_first_not_of("0123456789abcdef")==std::string::npos;
  }
  static Json state(const char* decision,const char* reason,uint64_t revision) {
    auto out=object();put(out,"decision",string(decision));put(out,"reason",string(reason));
    put(out,"speaker",string(""));put(out,"speaker_id",string(""));
    put(out,"revision",number(revision));put(out,"used_for_permissions",boolean(false));return out;
  }
  static void bind(Json& out,const FinalKey& key) {
    put(out,"track_id",string(key.track));put(out,"start_sample",number(key.start));
    put(out,"end_sample",number(key.end));
  }
  static Json summary(const cJSON* out) {
    auto result=object();
    for(const auto* name:{"decision","reason","speaker","speaker_id","revision",
                         "used_for_permissions","evidence_scope","speaker_uuid","registry_revision","continuity","display_label"})
      if(field(out,name))put(result,name,clone(field(out,name)));
    return result;
  }
  Row& lookup(uint64_t core_sequence,const FinalKey& key) {
    auto it=rows_.find(core_sequence);
    require(key.session==session_ && it!=rows_.end() && it->second.key==key,
            "stale or mismatched attribution final");return it->second;
  }
  template<class Select> std::vector<Json> retire(const std::string& reason,Select select) {
    std::vector<Json> out;
    for(const auto id:order_) {
      auto& row=rows_.at(id);if(!row.pending || !select(row))continue;
      auto item=state("uncertain",reason.c_str(),1);bind(item,row.key);
      put(item,"refers_to",number(row.key.sequence));put(item,"late",boolean(true));
      row.state=encode(summary(item.get()));row.pending=false;row.locally_retired=true;
      row.resolution=encode(item);out.push_back(std::move(item));
    }
    return out;
  }
};
} // namespace aii::voice::wire
