#pragma once
#include <algorithm>
#include <array>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
namespace aii::voice {
// Speaking languages of the compact speech model. English is the model at the
// root of the bound speech directory. Every other language is its own model,
// tokenizer, configuration and voice presets under languages/<directory>,
// present only where that language's files are installed. Recognition is a
// different model and stays English.
struct SpeechLanguage { const char* code; const char* label; const char* directory; };
inline constexpr std::array<SpeechLanguage,7> speech_languages={{
  {"en","English",""},{"fr","French","french"},{"de","German","german"},{"es","Spanish","spanish"},
  {"it","Italian","italian"},{"pt","Portuguese","portuguese"},{"nl","Dutch","dutch"}}};
inline const SpeechLanguage* speech_language(const std::string& code) {
  for(const auto& language:speech_languages) if(code==language.code) return &language;
  return nullptr;
}
// The model's own configuration names the characters its training text never
// contained and what stands in for each (`replace_characters`). Only that one
// block is read here, in the forms its publisher writes: two-space indented
// `from: to` rows of quoted scalars. A single-quoted scalar doubles a quote to
// mean one; a double-quoted scalar is taken only when it has no backslash, so
// no escape is ever interpreted. Any other row inside the block refuses, so a
// table this reader cannot represent is never applied in part.
using SpeechReplacements=std::vector<std::pair<std::string,std::string>>;
inline SpeechReplacements speech_replacements(const std::string& yaml) {
  SpeechReplacements table; bool inside=false; size_t at=0;
  auto scalar=[](const std::string& line,size_t& i) {
    if(i>=line.size() || (line[i]!='\'' && line[i]!='"')) throw std::invalid_argument("speech replacement row is not quoted");
    std::string value;
    if(line[i]=='"') {
      const auto close=line.find('"',i+1);
      if(close==std::string::npos) throw std::invalid_argument("speech replacement scalar is not closed");
      value=line.substr(i+1,close-i-1);
      if(value.find('\\')!=std::string::npos) throw std::invalid_argument("speech replacement scalar uses an escape");
      i=close+1; return value;
    }
    for(++i;;++i) {
      if(i>=line.size()) throw std::invalid_argument("speech replacement scalar is not closed");
      if(line[i]!='\'') { value.push_back(line[i]); continue; }
      if(i+1<line.size() && line[i+1]=='\'') { value.push_back('\''); ++i; continue; }
      ++i; return value;
    }
  };
  while(at<=yaml.size()) {
    const auto end=std::min(yaml.find('\n',at),yaml.size());
    auto line=yaml.substr(at,end-at); at=end+1;
    if(!line.empty() && line.back()=='\r') line.pop_back();
    if(!inside) { inside=line=="replace_characters:"; continue; }
    if(line.empty() || line[0]=='#') continue;
    if(line[0]!=' ') break; // the next top-level key ends the block
    if(line.rfind("  ",0)!=0 || (line.size()>2 && line[2]==' ')) throw std::invalid_argument("speech replacement row indentation differs");
    if(line.size()>2 && line[2]=='#') continue;
    size_t i=2; auto from=scalar(line,i);
    if(from.empty() || i+1>=line.size() || line[i]!=':' || line[i+1]!=' ') throw std::invalid_argument("speech replacement row shape differs");
    i+=2; auto to=scalar(line,i);
    if(i!=line.size()) throw std::invalid_argument("speech replacement row has trailing text");
    if(table.size()==64) throw std::invalid_argument("speech replacement table exceeds its bound");
    table.emplace_back(std::move(from),std::move(to));
  }
  return table;
}
// Longest source first, one pass: a replacement's own output is never replaced
// again, and the input's bytes outside every source are kept exactly.
inline std::string replace_speech_characters(const SpeechReplacements& table,const std::string& text) {
  if(table.empty()) return text;
  std::string out; out.reserve(text.size());
  for(size_t i=0;i<text.size();) {
    const std::pair<std::string,std::string>* best=nullptr;
    for(const auto& row:table)
      if(text.compare(i,row.first.size(),row.first)==0 && (!best || row.first.size()>best->first.size())) best=&row;
    if(best) { out+=best->second; i+=best->first.size(); }
    else out.push_back(text[i++]);
  }
  return out;
}
}
