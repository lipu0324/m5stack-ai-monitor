#pragma once

// Settings is a modal view; ordinary navigation always stays in the four tabs.
struct Navigation {
  int page=0, previousPage=0, setting=0;
  static int tab(int value) { return ((value%4)+4)%4; }
  void move(int value) { page=tab(value); }
  void enterSettings() { previousPage=tab(page);page=4;setting=0; }
  void leaveSettings() { page=tab(previousPage); }
  void toggleSettings() { if(page==4) leaveSettings();else enterSettings(); }
  void selectSetting(int delta) { setting=((setting+delta)%5+5)%5; }
};
