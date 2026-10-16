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
  // A side of a rule is at most max_chars characters (code points), counted
  // as the operations' input schemas count them, so that what an operator is
  // asked to confirm is what this list holds. Four bytes a character bound a
  // side's bytes: a reader holds a string to max_bytes before it is decoded,
  // and a longer text is refused for its length without being read.
  static constexpr size_t max_rules=64,max_chars=64,max_bytes=4*max_chars,max_words=4;
  // The longest `meant` the recognizer takes as a term to prefer
  // (aii_voice_models_prefer; TermBoost::max_bytes). A longer one is still a
  // rule: it is only never offered to the recognizer.
  static constexpr size_t max_term_bytes=64;
  // Refuses (std::invalid_argument) a rule this list will not hold. `heard`
  // is one to four words of letters, digits and apostrophes, single-spaced;
  // `meant` is any short single-line text that differs from it. Both are
  // well-formed UTF-8 an operator can read as written (see readable); what
  // was meant may be joined as its spelling joins it, what was heard may not.
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
  // The text's code points, or a refusal. Well-formed UTF-8 only: every value
  // in its shortest form, no surrogate, nothing above U+10FFFF. A looser
  // reader downstream would otherwise find a character in these bytes (a line
  // feed written in three) that was never judged here.
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
  // A rule is confirmed by an operator reading it, and what it writes is read
  // as a speaker's words. So neither side holds a character that cannot be
  // read where it stands: one that ends the line, or one with no shape of its
  // own that hides in the text or turns its neighbours around (zero-width
  // characters, direction marks and overrides, tags).
  //
  // These are the Unicode general categories Cc, Zl and Zp (ends_line) and Cf
  // (format), whole, from DerivedGeneralCategory.txt of Unicode 16.0.0; 15.0.0
  // and 17.0.0 list the same code points. The carrier asks its toolchain's
  // tables instead, and spec/correction_vectors.json holds both to one table
  // over every code point: when Unicode adds to these categories a test fails
  // on the side that moved, before a list one side wrote is refused by the other.
  static bool ends_line(char32_t c) { return c<0x20 || (c>=0x7f && c<=0x9f) || c==0x2028 || c==0x2029; }
  static bool format(char32_t c) {
    static constexpr char32_t ranges[][2]={
      {0xad,0xad},{0x600,0x605},{0x61c,0x61c},{0x6dd,0x6dd},{0x70f,0x70f},{0x890,0x891},{0x8e2,0x8e2},{0x180e,0x180e},
      {0x200b,0x200f},{0x202a,0x202e},{0x2060,0x2064},{0x2066,0x206f},{0xfeff,0xfeff},{0xfff9,0xfffb},
      {0x110bd,0x110bd},{0x110cd,0x110cd},{0x13430,0x1343f},{0x1bca0,0x1bca3},{0x1d173,0x1d17a},{0xe0001,0xe0001},{0xe0020,0xe007f}};
    for(const auto& range:ranges) if(c>=range[0] && c<=range[1]) return true;
    return false;
  }
  // What was meant is written in any script, and two format characters are
  // ordinary spelling there: the zero width non-joiner and joiner (U+200C,
  // U+200D). They are taken where spelling puts them, by the rule a speaker's
  // label has (runtime/native_uid/identity.cpp): inside a word, between two
  // characters that are neither a space (general category Zs, whole, from the
  // same Unicode data) nor a joiner. Persian and Indic words and joined emoji
  // are written with them. `unjoined` is the refusal of one that stands
  // anywhere else, and is given for what was meant only. What was heard takes
  // neither: it is what the recognizer writes, and nothing here establishes
  // that the recognizer writes them.
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
