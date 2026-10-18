#pragma once
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
namespace aii::voice {
struct Correction { std::string heard, meant; };
struct Corrected { std::string text; uint32_t applied=0; };
class Corrections {
 public:
  static constexpr size_t max_rules=64,max_chars=64,max_bytes=4*max_chars,max_words=4;
  static constexpr size_t max_term_bytes=64;
  static void validate(const Correction& rule) {
    words(rule.heard);
    const auto& m=rule.meant;
    if(m.empty() || m.size()>max_bytes) throw std::invalid_argument("meant must be 1..64 characters");
    if(m.front()==' ' || m.back()==' ') throw std::invalid_argument("meant must not begin or end with a space");
    readable(m,"meant must be 1..64 characters","meant must be one line of text","meant must not hold format characters, such as zero-width and direction marks",
             "meant holds a joiner outside a word; a joiner is taken only inside a word");
    if(m==rule.heard) throw std::invalid_argument("heard and meant are the same");
  }
  const std::vector<Correction>& rules() const { return rules_; }
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
  static std::u32string scalars(const std::string& s) {
    static constexpr char32_t least[]={0,0x80,0x800,0x10000};
    std::u32string out;
    for(size_t i=0;i<s.size();) {
      const unsigned char lead=static_cast<unsigned char>(s[i++]);
      const size_t more=lead<0x80?0:lead>=0xc2&&lead<=0xdf?1:lead>=0xe0&&lead<=0xef?2:lead>=0xf0&&lead<=0xf4?3:4;
      if(more==4 || more>s.size()-i) throw std::invalid_argument("text must be valid UTF-8");
      char32_t c=char32_t(more?lead&(0x3f>>more):lead);
      for(size_t k=0;k<more;++k,++i) {
        const unsigned char next=static_cast<unsigned char>(s[i]);
        if((next&0xc0)!=0x80) throw std::invalid_argument("text must be valid UTF-8");
        c=(c<<6)|char32_t(next&0x3f);
      }
      if(c<least[more] || c>0x10ffff || (c>=0xd800 && c<=0xdfff)) throw std::invalid_argument("text must be valid UTF-8");
      out.push_back(c);
    }
    return out;
  }
  static bool ends_line(char32_t c) { return c<0x20 || (c>=0x7f && c<=0x9f) || c==0x2028 || c==0x2029; }
  static bool format(char32_t c) {
    static constexpr char32_t ranges[][2]={
      {0xad,0xad},{0x600,0x605},{0x61c,0x61c},{0x6dd,0x6dd},{0x70f,0x70f},{0x890,0x891},{0x8e2,0x8e2},{0x180e,0x180e},
      {0x200b,0x200f},{0x202a,0x202e},{0x2060,0x2064},{0x2066,0x206f},{0xfeff,0xfeff},{0xfff9,0xfffb},
      {0x110bd,0x110bd},{0x110cd,0x110cd},{0x13430,0x1343f},{0x1bca0,0x1bca3},{0x1d173,0x1d17a},{0xe0001,0xe0001},{0xe0020,0xe007f}};
    for(const auto& range:ranges) if(c>=range[0] && c<=range[1]) return true;
    return false;
  }
  static bool joiner(char32_t c) { return c==0x200c || c==0x200d; }
  static bool space(char32_t c) {
    return c==0x20 || c==0xa0 || c==0x1680 || (c>=0x2000 && c<=0x200a) || c==0x202f || c==0x205f || c==0x3000;
  }
  static void readable(const std::string& s,const char* length,const char* line,const char* hidden,const char* unjoined=nullptr) {
    const auto points=scalars(s);
    if(points.size()>max_chars) throw std::invalid_argument(length);
    const auto joins=[&](size_t at) { return !joiner(points[at]) && !space(points[at]); };
    for(size_t i=0;i<points.size();++i) {
      const char32_t c=points[i];
      if(ends_line(c)) throw std::invalid_argument(line);
      if(unjoined && joiner(c)) {
        if(i==0 || i+1==points.size() || !joins(i-1) || !joins(i+1)) throw std::invalid_argument(unjoined);
        continue;
      }
      if(format(c)) throw std::invalid_argument(hidden);
    }
  }
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
    if(heard.empty() || heard.size()>max_bytes) throw std::invalid_argument("heard must be 1..64 characters");
    readable(heard,"heard must be 1..64 characters","heard is words of letters, digits and apostrophes","heard must not hold format characters, such as zero-width and direction marks");
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
      if(k+1==n && word.size()==rule.words[k].size()+2 && word.compare(0,rule.words[k].size(),rule.words[k])==0 &&
         word.compare(word.size()-2,2,"'s")==0) {
        const auto& last=spans[at+k];
        suffix=text.compare(last.end-2,2,"'s")==0||text.compare(last.end-2,2,"'S")==0?2:4;
        continue;
      }
      return false;
    }
    return true;
  }
};
}
