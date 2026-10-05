#pragma once
#include <stddef.h>
#include <stdint.h>

// ArduinoJson custom reader: bounded Content-Length, actual byte accounting,
// no body-sized String allocation, and no spinning on an idle network socket.
template<class Client> class ResponseReader {
  Client& client;
  uint32_t (*clock)();
  void (*pause)();
  uint32_t began,lastProgress,idleLimit,totalLimit;
public:
  const size_t expected;
  size_t received=0;
  bool timedOut=false;
  ResponseReader(Client& source,size_t length,uint32_t (*now)(),void (*yield)(),
                 uint32_t idle=2500,uint32_t total=5000)
    :client(source),clock(now),pause(yield),began(now()),lastProgress(began),
     idleLimit(idle),totalLimit(total),expected(length) {}
  size_t readBytes(char* buffer,size_t length) {
    if(length>expected-received) length=expected-received;
    size_t written=0;
    while(written<length) {
      uint32_t now=clock();
      if(now-lastProgress>=idleLimit || now-began>=totalLimit) {timedOut=true;break;}
      int available=client.available();
      if(available>0) {
        size_t wanted=length-written;
        if(wanted>(size_t)available) wanted=available;
        int count=client.read(reinterpret_cast<uint8_t*>(buffer+written),wanted);
        if(count>0) {written+=count;received+=count;lastProgress=clock();continue;}
      }
      // Drain already-buffered bytes before checking a peer's closed socket.
      if(!client.connected()) break;
      pause();
    }
    return written;
  }
  int read() {
    char byte;
    return readBytes(&byte,1)==1?static_cast<unsigned char>(byte):-1;
  }
  bool finish() {
    bool whitespace=true;
    while(received<expected) {
      int byte=read();
      if(byte<0) return false;
      if(byte!=' ' && byte!='\n' && byte!='\r' && byte!='\t') whitespace=false;
    }
    return whitespace;
  }
};
