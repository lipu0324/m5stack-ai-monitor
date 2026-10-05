"""Compile the firmware policy and verify transport-only recovery and cooldown."""
import subprocess
import tempfile
import unittest
from pathlib import Path


class LinkRecoveryTests(unittest.TestCase):
    def test_failures_cooldown_success_and_millis_wrap(self):
        root=Path(__file__).resolve().parents[1]
        program=r'''
#include <cassert>
#include "link_recovery.h"
int main() {
 LinkRecovery r;
 assert(!r.observe(true,false,1));assert(!r.observe(true,false,2));
 assert(r.observe(true,false,3));assert(r.rejoins==1 && r.failures==0);
 for(int i=4;i<500;i++) assert(!r.observe(true,false,i));
 assert(r.failures==255);assert(r.observe(true,false,60003));assert(r.rejoins==2);
 r.observe(false,true,60004);assert(r.failures==0);
 r.observe(true,false,60005);r.observe(false,false,60006);assert(r.failures==0);
 // HTTP auth/schema/server errors do not cause a radio reconnect.
 for(int i=0;i<100;i++) assert(!r.observe(false,false,200000+i));
 assert(r.rejoins==2);
 r.observe(true,false,200001);r.observe(false,true,200002);assert(r.failures==0);
 LinkRecovery wrap;wrap.observe(true,false,0xfffffff0U);wrap.observe(true,false,0xfffffff1U);
 assert(wrap.observe(true,false,0xfffffff2U));
 assert(!wrap.observe(true,false,20));assert(!wrap.observe(true,false,21));
 assert(!wrap.observe(true,false,22));assert(wrap.observe(true,false,60000));
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.cpp').write_text(program)
            subprocess.run(['c++','-std=c++11','-I',str(root/'src'),str(p/'test.cpp'),'-o',str(p/'test')],check=True,capture_output=True)
            subprocess.run([str(p/'test')],check=True)
