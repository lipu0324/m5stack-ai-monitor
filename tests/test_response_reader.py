"""Exercise the real firmware reader with fragmented, closed and stalled bodies."""
import subprocess
import tempfile
import unittest
from pathlib import Path


class ResponseReaderTests(unittest.TestCase):
    def test_fragmented_utf8_eof_timeouts_and_length_bound(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / '.pio/libdeps/m5stack-core/ArduinoJson/src/ArduinoJson.h').exists():
            self.skipTest('Run pio run first to install the firmware ArduinoJson dependency')
        program = r'''
#include <cassert>
#include <cstring>
#include <string>
#include <algorithm>
#include <ArduinoJson.h>
#include "response_reader.h"
uint32_t tick=0;
uint32_t clockTick() {return tick;}
void pauseTick() {tick++;}
struct Client {
  std::string bytes;
  size_t pos=0;
  bool open=false,stall=false,shortRead=false;
  int available() {return stall?0:std::min(size_t(7),bytes.size()-pos);}
  bool connected() {return open;}
  int read(uint8_t* out,size_t n) {
    n=std::min(n,bytes.size()-pos);
    if(shortRead) n=std::min(size_t(1),n);
    memcpy(out,bytes.data()+pos,n);pos+=n;return n;
  }
};
int main() {
  const std::string json=R"({"title":"中文主机","cpu":12.5})";
  // Peer is already closed, but all buffered bytes must still be consumed.
  Client closed{json};closed.shortRead=true;
  ResponseReader<Client> good(closed,json.size(),clockTick,pauseTick);
  DynamicJsonDocument doc(1024);
  assert(!deserializeJson(doc,good));assert(good.finish());
  assert(good.received==json.size());assert(doc["title"]=="中文主机");
  assert(doc["cpu"].as<double>()==12.5);
  Client shortChunks{"fragmented-body"};shortChunks.shortRead=true;
  ResponseReader<Client> fragments(shortChunks,shortChunks.bytes.size(),clockTick,pauseTick);
  char buffer[32]{};
  assert(fragments.readBytes(buffer,shortChunks.bytes.size())==shortChunks.bytes.size());
  assert(std::string(buffer)==shortChunks.bytes);assert(fragments.finish());
  // Content-Length exceeds EOF: the JSON value alone does not make it complete.
  Client truncated{json};
  ResponseReader<Client> partial(truncated,json.size()+4,clockTick,pauseTick);
  assert(!deserializeJson(doc,partial));assert(!partial.finish());
  assert(partial.received==json.size());
  Client whitespace{json+"\r\n "};
  ResponseReader<Client> spaced(whitespace,whitespace.bytes.size(),clockTick,pauseTick);
  assert(!deserializeJson(doc,spaced));assert(spaced.finish());
  Client garbage{json+"evil"};
  ResponseReader<Client> extra(garbage,garbage.bytes.size(),clockTick,pauseTick);
  assert(!deserializeJson(doc,extra));assert(!extra.finish());
  Client bounded{json+"extra"};
  ResponseReader<Client> limit(bounded,json.size(),clockTick,pauseTick);
  assert(!deserializeJson(doc,limit));assert(limit.finish());
  assert(bounded.pos==json.size());
  // An open silent socket cannot block indefinitely; it yields while waiting.
  tick=0;Client silent{json};silent.open=true;silent.stall=true;
  ResponseReader<Client> timeout(silent,json.size(),clockTick,pauseTick,10,50);
  assert(deserializeJson(doc,timeout));assert(timeout.timedOut && tick==10);
  // Test the absolute deadline independently of the idle timeout.
  tick=0;
  ResponseReader<Client> absolute(silent,json.size(),clockTick,pauseTick,50,10);
  assert(deserializeJson(doc,absolute));assert(absolute.timedOut && tick==10);
  Client broken{json.substr(0,7)};
  ResponseReader<Client> invalid(broken,json.size(),clockTick,pauseTick);
  assert(deserializeJson(doc,invalid)==DeserializationError::IncompleteInput);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'reader.cpp'
            binary = Path(directory) / 'reader'
            source.write_text(program)
            subprocess.run(['c++', '-std=c++17', '-I', str(root / 'src'),
                            '-I', str(root / '.pio/libdeps/m5stack-core/ArduinoJson/src'),
                            str(source), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)
