#pragma once
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
namespace aii::multitalker {
class TermBoost {
 public:
  static constexpr size_t max_terms=64,max_bytes=64,max_words=4;
  static constexpr float default_margin=4,default_budget=5;
  struct Step { int64_t token; uint16_t term,next; };
  TermBoost(const std::vector<std::string>& vocabulary,const std::vector<std::string>& terms,float margin,float budget)
      :margin_(margin),budget_(budget) {
    if(!(margin>=0) || !(budget>=margin) || budget>1024) throw std::invalid_argument("term boost margin and budget");
    if(terms.size()>max_terms) throw std::invalid_argument("at most 64 boosted terms");
    std::vector<std::string> folded; folded.reserve(vocabulary.size());
    for(const auto& piece:vocabulary) {
      folded.push_back(fold(piece));
      const auto first=piece.empty()?0:static_cast<unsigned char>(piece[0]);
      begins_.push_back(first==' ' && piece.size()>1);
      continues_.push_back(first>=0x80 || (first>='0'&&first<='9') || (first>='A'&&first<='Z') || (first>='a'&&first<='z') || first=='\'');
    }
    for(const auto& written:terms) {
      const auto text=" "+normal(written);
      for(const auto& held:texts_) if(held==text) throw std::invalid_argument("a boosted term is listed twice");
      const auto term=uint16_t(texts_.size());
      auto steps=paths(" "+written,vocabulary);
      if(steps.empty()) steps=paths(text,folded);
      if(steps.empty()) throw std::invalid_argument("the recognizer's word pieces cannot spell a boosted term");
      for(const auto& step:steps[0]) starts_.push_back({step.first,term,step.second});
      uint16_t words=0; for(const char c:text) words=uint16_t(words+(c==' '));
      texts_.push_back(text); steps_.push_back(std::move(steps)); words_.push_back(words);
    }
  }
  bool continues(int64_t token) const { return token>=0 && size_t(token)<continues_.size() && continues_[size_t(token)]; }
  bool begins(int64_t token) const { return token>=0 && size_t(token)<begins_.size() && begins_[size_t(token)]; }
  uint16_t words(uint16_t term) const { return words_.at(term); }
  bool empty() const { return texts_.empty(); }
  size_t size() const { return texts_.size(); }
  float margin() const { return margin_; }
  float budget() const { return budget_; }
  const std::vector<Step>& starts() const { return starts_; }
  const std::vector<std::pair<int64_t,uint16_t>>& steps(uint16_t term,uint16_t at) const { return steps_.at(term).at(at); }
  bool complete(uint16_t term,uint16_t at) const { return at==texts_.at(term).size(); }
  static std::string normal(const std::string& written) {
    if(written.empty() || written.size()>max_bytes) throw std::invalid_argument("a boosted term must be 1..64 bytes");
    size_t words=1; bool letter=false;
    for(size_t i=0;i<written.size();++i) {
      const auto c=static_cast<unsigned char>(written[i]);
      if(c==' ') {
        if(!letter) throw std::invalid_argument("a boosted term is words separated by single spaces");
        ++words; letter=false; continue;
      }
      if(!(c>=0x80 || (c>='0'&&c<='9') || (c>='A'&&c<='Z') || (c>='a'&&c<='z') || c=='\''))
        throw std::invalid_argument("a boosted term is words of letters, digits and apostrophes");
      letter=letter||c!='\'';
    }
    if(!letter || words>max_words) throw std::invalid_argument("a boosted term is one to four words");
    return fold(written);
  }
 private:
  using Steps=std::vector<std::vector<std::pair<int64_t,uint16_t>>>;
  static Steps paths(const std::string& text,const std::vector<std::string>& pieces) {
    Steps steps(text.size());
    for(size_t at=0;at<text.size();++at) for(size_t id=0;id<pieces.size();++id) {
      const auto& piece=pieces[id];
      if(piece.empty() || piece.size()>text.size()-at || text.compare(at,piece.size(),piece)!=0) continue;
      steps[at].push_back({int64_t(id),uint16_t(at+piece.size())});
    }
    std::vector<bool> reaches(text.size()+1,false); reaches[text.size()]=true;
    for(size_t at=text.size();at-->0;) {
      std::vector<std::pair<int64_t,uint16_t>> kept;
      for(const auto& step:steps[at]) if(reaches[step.second]) kept.push_back(step);
      steps[at]=std::move(kept); reaches[at]=!steps[at].empty();
    }
    if(!reaches[0]) { steps.clear(); return steps; }
    std::vector<std::pair<int64_t,uint16_t>> lettered;
    for(const auto& step:steps[0]) if(step.second>1) lettered.push_back(step);
    if(!lettered.empty()) steps[0]=std::move(lettered);
    return steps;
  }
  static std::string fold(std::string s) {
    for(auto& c:s) if(c>='A'&&c<='Z') c=char(c-'A'+'a');
    return s;
  }
  float margin_,budget_;
  std::vector<std::string> texts_;
  std::vector<Steps> steps_;
  std::vector<Step> starts_;
  std::vector<bool> continues_,begins_;
  std::vector<uint16_t> words_;
};
}
