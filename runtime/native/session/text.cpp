#include "text.h"
#include <cstdint>
#include <stdexcept>

namespace aii::voice {
namespace {
struct Rune { uint32_t value; size_t byte; };
bool space(uint32_t c) {
  return (c>=9 && c<=13) || (c>=0x1c && c<=0x20) || c==0x85 || c==0xa0 || c==0x1680 ||
         (c>=0x2000 && c<=0x200a) || c==0x2028 || c==0x2029 || c==0x202f || c==0x205f || c==0x3000;
}
std::vector<Rune> decode(const std::string& text) {
  if(text.size()>32000) throw std::invalid_argument("speech exceeds 8000 UTF-8 characters");
  std::vector<Rune> runes;
  for(size_t i=0;i<text.size();) {
    const size_t start=i;
    const auto lead=static_cast<unsigned char>(text[i++]);
    uint32_t c=lead;size_t continuation=0;uint32_t minimum=0;
    if(lead<0x80) {}
    else if(lead>=0xc2 && lead<=0xdf) { c=lead&0x1f;continuation=1;minimum=0x80; }
    else if(lead>=0xe0 && lead<=0xef) { c=lead&0xf;continuation=2;minimum=0x800; }
    else if(lead>=0xf0 && lead<=0xf4) { c=lead&7;continuation=3;minimum=0x10000; }
    else throw std::invalid_argument("invalid UTF-8 speech lead");
    for(size_t k=0;k<continuation;++k) {
      if(i==text.size()) throw std::invalid_argument("truncated UTF-8 speech");
      const auto b=static_cast<unsigned char>(text[i++]);
      if((b&0xc0)!=0x80) throw std::invalid_argument("invalid UTF-8 speech continuation");
      c=(c<<6)|(b&0x3f);
    }
    if(c<minimum || c>0x10ffff || (c>=0xd800 && c<=0xdfff)) throw std::invalid_argument("invalid UTF-8 speech scalar");
    if(c<32 && c!=9 && c!=10 && c!=13) throw std::invalid_argument("control character in speech");
    runes.push_back({c,start});
    if(runes.size()>8000) throw std::invalid_argument("speech exceeds 8000 characters");
  }
  return runes;
}
bool closer(uint32_t c) { return c=='"' || c=='\'' || c==0x2019 || c==0x201d || c==')'; }
}
std::string strip_text(const std::string& text) {
  const auto runes=decode(text);size_t first=0,last=runes.size();
  while(first<last && space(runes[first].value))++first;
  while(last>first && space(runes[last-1].value))--last;
  if(first==last)return {};
  return text.substr(runes[first].byte,(last==runes.size()?text.size():runes[last].byte)-runes[first].byte);
}
std::vector<std::string> split_text(const std::string& text,size_t limit) {
  if(limit<32 || limit>512)throw std::invalid_argument("segment limit must be 32..512 characters");
  const auto runes=decode(text);
  bool content=false;for(const auto& r:runes)content|=!space(r.value);
  if(!content)throw std::invalid_argument("nonempty speech required");
  std::vector<std::string> result;size_t start=0;
  while(runes.size()-start>limit) {
    const size_t bound=start+limit;size_t sentence=0,whitespace=0;
    for(size_t i=start;i<bound;) {
      if(space(runes[i].value)) {
        size_t end=i+1;while(end<bound && space(runes[end].value))++end;
        whitespace=end;i=end;
      } else ++i;
    }
    for(size_t i=start;i<bound;) {
      const auto c=runes[i].value;size_t end=i;
      if(c=='.' || c=='!' || c=='?') {
        end=i+1;while(end<bound && closer(runes[end].value))++end;
        const auto first_space=end;while(end<bound && space(runes[end].value))++end;
        if(end==first_space)end=i;
      } else if(c=='\n') {end=i+1;while(end<bound && runes[end].value=='\n')++end;}
      if(end>i) {sentence=end;i=end;}else ++i;
    }
    const size_t end=sentence?sentence:whitespace;
    if(!end)throw std::invalid_argument("unbroken word exceeds segment limit");
    result.push_back(text.substr(runes[start].byte,runes[end].byte-runes[start].byte));start=end;
  }
  if(start<runes.size())result.push_back(text.substr(runes[start].byte));
  return result;
}
}
