#pragma once
#include <stdint.h>

// A connected STA can lose its usable IP/TCP path. Rejoin after repeated
// transport failures, with a cooldown when the actual server stays offline.
struct LinkRecovery {
  uint8_t failures=0;
  uint32_t rejoins=0,lastRejoin=0;
  bool rejoined=false;
  bool observe(bool transportFailure,bool success,uint32_t now) {
    if(success || !transportFailure) {failures=0;return false;}
    if(failures<255) failures++;
    if(failures<3 || (rejoined && uint32_t(now-lastRejoin)<60000U)) return false;
    failures=0;rejoins++;lastRejoin=now;rejoined=true;return true;
  }
};
