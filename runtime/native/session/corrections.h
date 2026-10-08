#pragma once
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
namespace aii::voice {
// A correction list: what the recognizer writes for a name it does not know,
// and what was meant. It is taught, never trained: no model changes.
//
// A rule is blind to context. It rewrites its words wherever they stand, so it
// is for mis-hearings that are never the right words where this engine
// listens. Recognized speech is recorded as the speaker's own words; a rule
// changes that record, which is why the list changes only through confirmed
// operations and why a rewritten transcript carries what was heard.
struct Correction { std::string heard, meant; };
struct Corrected { std::string text; uint32_t applied=0; };
class Corrections {
 public:
  static constexpr size_t max_rules=64,max_bytes=64,max_words=4;
  // Refuses (std::invalid_argument) a rule this list will not hold. `heard`
  // is one to four words of letters, digits and apostrophes, single-spaced;
  // `meant` is any short single-line text that differs from it.
  static void validate(const Correction& rule) {
    words(rule.heard);
    const auto& m=rule.meant;
    if(m.empty() || m.size()>max_bytes) throw std::invalid_argument("meant must be 1..64 bytes");
    if(m.front()==' ' || m.back()==' ') throw std::invalid_argument("meant must not begin or end with a space");
    for(unsigned char c:m) if(c<0x20 || c==0x7f) throw std::invalid_argument("meant must be one line of text");
    require_utf8(m);
    if(m==rule.heard) throw std::invalid_argument("heard and meant are the same");
  }
  const std::vector<Correction>& rules() const { return rules_; }
  // One rule per heard phrase, compared without case. Teaching a phrase again
  // replaces what it meant. Returns whether the list changed.
  bool correct(const Correction& rule) {
    validate(rule);
    const auto key=fold(rule.heard);
    for(auto& held:rules_) if(fold(held.heard)==key) {
      if(held.heard==rule.heard && held.meant==rule.meant) return false;
      held=rule; index(); return true;
    }
    if(rules_.size()==max_rules) throw std::invalid_argument("the correction list is full (64 rules)");
    rules_.push_back(rule); index(); return true;
  }
  bool forget(const std::string& heard) {
    const auto key=fold(heard);
    for(size_t i=0;i<rules_.size();++i) if(fold(rules_[i].heard)==key) {
      rules_.erase(rules_.begin()+std::ptrdiff_t(i)); index(); return true;
    }
    return false;
  }
  void replace(std::vector<Correction> rules) {
    if(rules.size()>max_rules) throw std::invalid_argument("the correction list is full (64 rules)");
    Corrections next;
    for(const auto& rule:rules) {
      const auto before=next.rules_.size();
      next.correct(rule);
      if(next.rules_.size()==before) throw std::invalid_argument("a correction is listed twice");
    }
    *this=std::move(next);
  }
  // One pass over what was recognized. At each word the rule with the most
  // words wins; what a rule wrote is never read again, so rules do not chain.
  // A phrase matches whole words, without case, joined by spaces only: never
  // across punctuation and never inside a longer word. A possessive ('s) on
  // the last word is kept. Every byte outside a match is left as it was.
  Corrected apply(const std::string& text) const {
    Corrected out;
    if(rules_.empty() || text.empty()) { out.text=text; return out; }
    std::vector<Span> spans; scan(text,spans);
    out.text.reserve(text.size());
    size_t copied=0;
    for(size_t w=0;w<spans.size();) {
      const Indexed* best=nullptr; size_t tail=0;
      for(const auto& rule:indexed_) {
        if(best && rule.words.size()<=best->words.size()) continue;
        size_t suffix=0;
        if(matches(text,spans,w,rule,suffix)) { best=&rule; tail=suffix; }
      }
      if(!best) { ++w; continue; }
      const auto& last=spans[w+best->words.size()-1];
      const auto& meant=rules_[best->rule].meant;
      // Already written as meant: nothing was corrected and nothing is counted.
      if(text.compare(spans[w].begin,last.end-tail-spans[w].begin,meant)==0) { w+=best->words.size(); continue; }
      out.text.append(text,copied,spans[w].begin-copied);
      out.text+=meant;
      out.text.append(text,last.end-tail,tail);
      copied=last.end; ++out.applied; w+=best->words.size();
    }
    out.text.append(text,copied,std::string::npos);
    return out;
  }
 private:
  struct Span { size_t begin,end; };
  struct Indexed { size_t rule; std::vector<std::string> words; };
  std::vector<Correction> rules_;
  std::vector<Indexed> indexed_;
  static bool word_byte(unsigned char c) {
    return c>=0x80 || (c>='0'&&c<='9') || (c>='A'&&c<='Z') || (c>='a'&&c<='z') || c=='\'';
  }
  static std::string fold(std::string s) {
    for(auto& c:s) if(c>='A'&&c<='Z') c=char(c-'A'+'a');
    return s;
  }
  static void require_utf8(const std::string& s) {
    for(size_t i=0;i<s.size();) {
      const unsigned char lead=static_cast<unsigned char>(s[i]);
      const size_t more=lead<0x80?0:lead>=0xc2&&lead<=0xdf?1:lead>=0xe0&&lead<=0xef?2:lead>=0xf0&&lead<=0xf4?3:4;
      if(more==4 || (more && i+more>=s.size())) throw std::invalid_argument("text must be valid UTF-8");
      for(size_t k=1;k<=more;++k) if((static_cast<unsigned char>(s[i+k])&0xc0)!=0x80) throw std::invalid_argument("text must be valid UTF-8");
      i+=more+1;
    }
  }
  // The typographic apostrophe (U+2019) is the same letter as the plain one
  // for matching; recognizers write either.
  static void scan(const std::string& text,std::vector<Span>& spans) {
    for(size_t i=0;i<text.size();) {
      if(!word_byte(static_cast<unsigned char>(text[i]))) { ++i; continue; }
      const size_t begin=i;
      while(i<text.size() && word_byte(static_cast<unsigned char>(text[i]))) ++i;
      spans.push_back({begin,i});
    }
  }
  static std::string plain(const std::string& text,const Span& span) {
    std::string word;
    for(size_t i=span.begin;i<span.end;++i) {
      if(i+2<span.end && text.compare(i,3,"\xe2\x80\x99")==0) { word.push_back('\''); i+=2; }
      else word.push_back(text[i]);
    }
    return fold(std::move(word));
  }
  static std::vector<std::string> words(const std::string& heard) {
    if(heard.empty() || heard.size()>max_bytes) throw std::invalid_argument("heard must be 1..64 bytes");
    require_utf8(heard);
    std::vector<std::string> out; std::string word;
    for(size_t i=0;i<=heard.size();++i) {
      const unsigned char c=i<heard.size()?static_cast<unsigned char>(heard[i]):' ';
      if(c==' ') {
        if(word.empty()) throw std::invalid_argument("heard is words separated by single spaces");
        out.push_back(fold(word)); word.clear(); continue;
      }
      if(!word_byte(c)) throw std::invalid_argument("heard is words of letters, digits and apostrophes");
      word.push_back(char(c));
    }
    if(out.size()>max_words) throw std::invalid_argument("heard is at most four words");
    for(auto& w:out) {
      std::string normal;
      for(size_t i=0;i<w.size();++i) {
        if(i+2<w.size() && w.compare(i,3,"\xe2\x80\x99")==0) { normal.push_back('\''); i+=2; }
        else normal.push_back(w[i]);
      }
      bool letter=false; for(unsigned char c:normal) letter|=c!='\'';
      if(!letter) throw std::invalid_argument("heard needs a letter or digit in every word");
      w=std::move(normal);
    }
    return out;
  }
  void index() {
    indexed_.clear();
    for(size_t i=0;i<rules_.size();++i) indexed_.push_back({i,words(rules_[i].heard)});
  }
  static bool only_spaces(const std::string& text,size_t from,size_t to) {
    if(from==to) return false;
    for(size_t i=from;i<to;++i) if(text[i]!=' ') return false;
    return true;
  }
  static bool matches(const std::string& text,const std::vector<Span>& spans,size_t at,const Indexed& rule,size_t& suffix) {
    const size_t n=rule.words.size();
    if(at+n>spans.size()) return false;
    for(size_t k=0;k<n;++k) {
      if(k && !only_spaces(text,spans[at+k-1].end,spans[at+k].begin)) return false;
      const auto word=plain(text,spans[at+k]);
      if(word==rule.words[k]) continue;
      // The speaker's possessive is theirs to keep: "Kwin's" becomes "Quinn's".
      if(k+1==n && word.size()==rule.words[k].size()+2 && word.compare(0,rule.words[k].size(),rule.words[k])==0 &&
         word.compare(word.size()-2,2,"'s")==0) {
        const auto& last=spans[at+k];
        suffix=text.compare(last.end-2,2,"'s")==0||text.compare(last.end-2,2,"'S")==0?2:4; // 's or the three-byte apostrophe and s
        continue;
      }
      return false;
    }
    return true;
  }
};
}
