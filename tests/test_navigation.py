"""Exercise the exact navigation state machine used by the ESP32 firmware."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

class NavigationTests(unittest.TestCase):
    def test_tab_cycle_and_settings_returns(self):
        compiler=shutil.which('c++')
        self.assertIsNotNone(compiler,'native C++ compiler required for navigation regression')
        root=Path(__file__).resolve().parents[1]
        program=r'''
#include <cassert>
#include "navigation.h"
int main() {
  Navigation n;
  for(int i=0;i<20;i++) {n.move(n.page+1);assert(n.page==(i+1)%Navigation::TAB_COUNT);}
  for(int i=0;i<20;i++) {n.move(n.page-1);assert(n.page>=0 && n.page<Navigation::TAB_COUNT);}
  for(int origin=0;origin<Navigation::TAB_COUNT;origin++) {
    n.move(origin);n.enterSettings();assert(n.page==Navigation::SETTINGS && n.setting==0);
    for(int i=0;i<10;i++) {n.selectSetting(1);assert(n.setting==(i+1)%5);assert(n.page==Navigation::SETTINGS);}
    n.selectSetting(-1);assert(n.setting==4);n.leaveSettings();assert(n.page==origin);
    n.toggleSettings();assert(n.page==Navigation::SETTINGS);n.toggleSettings();assert(n.page==origin);
  }
  n.previousPage=-7;n.leaveSettings();assert(n.page==3);
  n.move(Navigation::TAB_COUNT);assert(n.page==0);
}
'''
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'navigation.cpp';out=Path(d)/'navigation';src.write_text(program)
            subprocess.run([compiler,'-std=c++11','-I',str(root/'src'),str(src),'-o',str(out)],check=True,capture_output=True)
            subprocess.run([str(out)],check=True)
