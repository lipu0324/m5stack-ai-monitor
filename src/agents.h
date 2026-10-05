#pragma once
#include <stdint.h>
#include <string.h>

// Stable IDs shared with host/agents.py; mask persists across discovery changes.
struct AgentSelection {
  static constexpr int COUNT=9;
  uint16_t hidden=0;
  static const char* id(int index) {
    const char* ids[]={"codex","hermes","opencode","claude","gemini","aider","goose","amp","cursor"};
    return index>=0 && index<COUNT?ids[index]:"unknown";
  }
  static const char* label(int index) {
    const char* names[]={"Codex","Hermes","OpenCode","Claude Code","Gemini CLI","Aider","Goose","Amp","Cursor Agent"};
    return index>=0 && index<COUNT?names[index]:"Agent";
  }
  static int index(const char* key) {
    for(int i=0;i<COUNT;i++) if(key && !strcmp(id(i),key)) return i;
    return -1;
  }
  bool enabled(int i) const {return i>=0 && i<COUNT && !(hidden & (1U<<i));}
  void toggle(int i) {if(i>=0 && i<COUNT) hidden^=1U<<i;}
  int next(int current,const bool* detected,bool onlyEnabled=false) const {
    for(int n=1;n<=COUNT;n++) {
      int i=(current+n+COUNT)%COUNT;
      if(detected[i] && (!onlyEnabled || enabled(i))) return i;
    }
    return -1;
  }
};
