#pragma once

// Settings is modal and stays outside the ordinary monitoring tabs.
struct Navigation {
  static constexpr int TAB_COUNT=5, SETTINGS=TAB_COUNT;
  int page=0, previousPage=0, setting=0;
  static int tab(int value) { return ((value%TAB_COUNT)+TAB_COUNT)%TAB_COUNT; }
  void move(int value) { page=tab(value); }
  void enterSettings() { previousPage=tab(page);page=SETTINGS;setting=0; }
  void leaveSettings() { page=tab(previousPage); }
  void toggleSettings() { if(page==SETTINGS) leaveSettings();else enterSettings(); }
  void selectSetting(int delta) { setting=((setting+delta)%5+5)%5; }
};
