#pragma once
#include <math.h>

constexpr uint16_t rgb(int r,int g,int b) {return ((r>>3)<<11)|((g>>2)<<5)|(b>>3);}
constexpr uint16_t UI_BG=rgb(9,17,28), UI_CARD=rgb(20,33,49), UI_SELECTED=rgb(26,48,65);
constexpr uint16_t UI_TEXT=rgb(237,245,251), UI_DIM=rgb(147,167,187), UI_LINE=rgb(40,58,76);
constexpr uint16_t UI_CYAN=rgb(63,205,239), UI_GREEN=rgb(74,222,157), UI_AMBER=rgb(255,194,88), UI_RED=rgb(255,108,119);
uint32_t stripHashes[3]{};
lgfx::LGFXBase* gfx=&M5.Display;
int stripY=0, sceneX=0;
float sceneProgress=1;

void box(int x,int y,int w,int h,uint16_t c,int radius=0) {
  if(radius) gfx->fillRoundRect(x+sceneX,y-stripY,w,h,radius,c);
  else gfx->fillRect(x+sceneX,y-stripY,w,h,c);
}
void stroke(int x,int y,int w,int h,uint16_t c,int radius=0) {
  if(radius) gfx->drawRoundRect(x+sceneX,y-stripY,w,h,radius,c);
  else gfx->drawRect(x+sceneX,y-stripY,w,h,c);
}
void segment(int x,int y,int xx,int yy,uint16_t c) {gfx->drawLine(x+sceneX,y-stripY,xx+sceneX,yy-stripY,c);}
void dot(int x,int y,int r,uint16_t c,bool fill=true) {
  if(fill) gfx->fillCircle(x+sceneX,y-stripY,r,c);else gfx->drawCircle(x+sceneX,y-stripY,r,c);
}
void label(const String& value,int x,int y,uint16_t c=UI_TEXT,int size=12) {
  if(size==26) gfx->setFont(&fonts::Font4);else if(size==16) gfx->setFont(&fonts::efontCN_16);else gfx->setFont(&fonts::efontCN_12);
  gfx->setTextColor(c);gfx->drawString(value,x+sceneX,y-stripY);
}
String fitted(const String& value,int width,int size=12) {
  gfx->setFont(size==16?&fonts::efontCN_16:&fonts::efontCN_12);
  if(gfx->textWidth(value)<=width) return value;
  String result;
  for(size_t i=0;i<value.length();) {
    uint8_t ch=value[i];int n=ch<0x80?1:(ch&0xE0)==0xC0?2:(ch&0xF0)==0xE0?3:4;
    String piece=value.substring(i,i+n);i+=n;
    if(gfx->textWidth(result+piece+"…")>width) break;
    result+=piece;
  }
  return result+"…";
}
void wrap(const String& value,int x,int y,int width,int lines,uint16_t c=UI_DIM,int size=12,int skip=0) {
  gfx->setFont(size==16?&fonts::efontCN_16:&fonts::efontCN_12);
  String line;int count=0;
  for(size_t i=0;i<value.length();) {
    uint8_t ch=value[i];int n=ch<0x80?1:(ch&0xE0)==0xC0?2:(ch&0xF0)==0xE0?3:4;
    String piece=value.substring(i,i+n);i+=n;
    if(piece=="\n" || gfx->textWidth(line+piece)>width) {
      if(count>=skip) label(line,x,y+(count-skip)*(size+2),c,size);
      count++;line="";if(count>=lines+skip) return;
    }
    if(piece!="\n") line+=piece;
  }
  if(count>=skip && count<lines+skip) label(line,x,y+(count-skip)*(size+2),c,size);
}
void pill(const String& value,int x,int y,uint16_t c,int width=42) {
  box(x,y,width,19,UI_SELECTED,5);label(value,x+6,y+3,c);
}
void progress(int x,int y,int width,int height,float fraction,uint16_t c) {
  box(x,y,width,height,UI_LINE,height/2);
  int filled=(int)(width*constrain(fraction,0.0f,1.0f)*sceneProgress);
  if(filled>0) box(x,y,max(height,filled),height,c,height/2);
}
void ring(int x,int y,int radius,float fraction,uint16_t c) {
  for(int i=0;i<60;i++) {
    float angle=(i*6-90)*PI/180;
    int xx=x+cosf(angle)*radius, yy=y+sinf(angle)*radius;
    dot(xx,yy,2,i<constrain(fraction,0.0f,1.0f)*60*sceneProgress?c:UI_LINE);
  }
}
void wifiIcon(int x,int y,uint16_t c) {
  dot(x,y+10,2,c);
  segment(x-4,y+5,x,y+3,c);segment(x,y+3,x+4,y+5,c);
  segment(x-8,y,x-3,y-3,c);segment(x-3,y-3,x+3,y-3,c);segment(x+3,y-3,x+8,y,c);
}
void key(const String& letter,const String& action,int x,int width=98) {
  box(x,225,16,13,UI_LINE,3);label(letter,x+4,225,UI_TEXT);label(action,x+21,225,UI_DIM);
}
void emptyState(const String& title,const String& subtitle,bool ok=true) {
  dot(160,104,30,UI_CARD);ring(160,104,29,1,ok?UI_GREEN:UI_CYAN);
  if(ok) {segment(147,104,157,114,UI_GREEN);segment(157,114,175,93,UI_GREEN);}
  else {dot(151,104,3,UI_CYAN);dot(160,104,3,UI_CYAN);dot(169,104,3,UI_CYAN);}
  gfx->setFont(&fonts::efontCN_16);label(title,(320-gfx->textWidth(title))/2,147,UI_TEXT,16);
  gfx->setFont(&fonts::efontCN_12);label(subtitle,(320-gfx->textWidth(subtitle))/2,175,UI_DIM);
}
void taskPage() {
  box(10,41,146,28,UI_CARD,7);dot(22,55,3,UI_CYAN);
  label("运行",32,47,UI_DIM);label(String(view.counts[RUNNING]),116,45,UI_CYAN,16);
  box(164,41,146,28,UI_CARD,7);dot(176,55,3,UI_AMBER);
  label("等待操作",186,47,UI_DIM);label(String(view.counts[APPROVAL]+view.counts[WAIT_INPUT]),278,45,UI_AMBER,16);
  if(!view.received || !view.count) {emptyState(view.received?"所有任务已空闲":"正在获取任务",view.received?"新任务会自动出现在这里":"连接成功后自动同步",view.received);return;}
  if(textPage) {
    Task& t=view.tasks[selection[0]];bool c=String(t.id).startsWith("codex:");
    box(10,76,300,125,UI_CARD,9);pill(c?"Codex":"Hermes",20,83,c?UI_CYAN:UI_GREEN,58);
    label(labels[t.status],90,86,color(t.status));label(elapsed(t.started),239,86,UI_DIM);
    wrap(t.title,20,111,280,2,UI_TEXT,16);
    label(fitted(t.action,276),20,154,UI_CYAN);
    wrap(t.summary,20,174,280,2,UI_DIM);
    return;
  }
  int first=(selection[0]/3)*3;
  for(int i=first;i<min(first+3,view.count);i++) {
    Task& t=view.tasks[i];bool chosen=i==selection[0],c=String(t.id).startsWith("codex:");int y=76+(i-first)*42;
    box(10,y,300,37,chosen?UI_SELECTED:UI_CARD,7);
    if(chosen) box(10,y+8,3,21,c?UI_CYAN:UI_GREEN,1);
    box(19,y+8,21,21,c?rgb(28,70,91):rgb(25,67,52),5);label(c?"C":"H",25,y+10,c?UI_CYAN:UI_GREEN,16);
    label(fitted(t.title,252,16),48,y+3,UI_TEXT,16);
    label(labels[t.status],48,y+22,color(t.status));
    label(elapsed(t.started),222,y+22,UI_DIM);
  }
  int visible=min(3,view.count-first);
  if(visible<3) {
    Task& t=view.tasks[selection[0]];int y=79+visible*42,h=202-y;
    box(10,y,300,h,UI_CARD,8);
    label(visible==1?"当前动作":"当前动作 · "+fitted(t.action,200),20,y+7,UI_DIM);
    if(visible==1) {
      label(fitted(t.action[0]?String(t.action):String("正在执行任务"),278,16),20,y+24,UI_CYAN,16);
      wrap(t.summary,20,y+46,280,2,UI_DIM);
    }
  }
}
void approvalPage(uint32_t now) {
  if(!view.approvalCount) {emptyState("暂无待审批命令","请求出现后可批准一次或拒绝");return;}
  Approval& a=view.approvals[selection[1]];
  pill(String(a.source)=="codex"?"Codex":"Hermes",10,41,UI_AMBER,62);
  label(String(selection[1]+1)+" / "+view.approvalCount,268,44,UI_DIM);
  label(fitted(a.title,292,16),12,64,UI_TEXT,16);
  box(10,86,300,104,UI_CARD,8);box(10,95,3,85,UI_AMBER,1);
  wrap(a.command,24,90,272,7,UI_AMBER);
  bool allowed=a.canApprove && commandFits(a.command);
  if(!allowed) label("命令未完整显示，请在电脑处理",14,195,UI_RED);
  else {
    label("按住 A 拒绝",18,195,UI_RED);label("按住 C 批准一次",192,195,UI_GREEN);
    if(!acSuppress && M5.BtnA.isPressed()) progress(14,190,138,3,(now-aPressedAt)/2000.0,UI_RED);
    if(!acSuppress && M5.BtnC.isPressed()) progress(168,190,138,3,(now-cPressedAt)/2000.0,UI_GREEN);
  }
}
void metricPage() {
  Metric& m=view.metrics[metricSource];uint16_t accent=metricSource?UI_GREEN:UI_CYAN;
  pill(metricSource?"Hermes":"Codex",10,41,accent,64);
  label(metricMode?"套餐额度":"Token / 缓存",84,44,UI_TEXT);
  label("B 切换来源",239,44,UI_DIM);
  if(metricMode) {
    if(!m.quotaAvailable || !m.quotaCount) {emptyState("暂未提供套餐额度","此来源未返回额度数据",false);return;}
    for(int i=0;i<min(m.quotaCount,2);i++) {
      Quota& q=m.quotas[i];int y=70+i*68;float remaining=constrain(100-q.used,0.0f,100.0f);
      box(10,y,300,62,UI_CARD,9);label(fitted(q.label,154),22,y+8,UI_DIM);
      label(String(remaining,0)+"%",213,y+5,remaining<10?UI_RED:accent,26);
      label("剩余",273,y+15,UI_DIM);
      progress(22,y+35,276,8,remaining/100,remaining<10?UI_RED:accent);
      label("重置倒计时 "+elapsed(nowEpoch(),q.reset),22,y+47,UI_DIM);
    }
    if(m.quotaCount==1) {
      box(10,138,300,54,UI_CARD,9);Quota& q=m.quotas[0];
      ring(39,165,16,constrain(q.used/100,0.0f,1.0f),UI_AMBER);
      label("已使用 "+String(q.used,0)+"%",70,148,UI_AMBER,16);
      label(String(q.minutes>=1440?"统计窗口 ":"统计窗口 ")+(q.minutes>=1440?String(q.minutes/1440)+" 天":String(q.minutes)+" 分钟")+" · 每分钟更新",70,174,UI_DIM);
    }
    label("账户共享额度 · 长按 C 切回用量",14,195,UI_DIM);return;
  }
  box(10,66,146,36,UI_CARD,7);box(164,66,146,36,UI_CARD,7);
  label("输入",19,71,UI_DIM);label(m.available?compactTokens(m.input):"--",59, 70,accent,26);
  label("输出",173,71,UI_DIM);label(m.available?compactTokens(m.output):"--",213,70,UI_TEXT,26);
  // Seven days with explicit scale and labels; missing metrics stay visibly unknown.
  box(10,108,300,59,UI_CARD,7);uint64_t peak=1;for(auto n:m.daily) peak=max(peak,n);
  label("7 日",18,112,UI_DIM);label(m.dailyAvailable?compactTokens(peak):"--",18,128,accent);
  segment(70,148,298,148,UI_LINE);segment(70,137,298,137,UI_LINE);
  if(m.dailyAvailable) for(int i=0;i<7;i++) {
    int x=76+i*32,h=(int)(22.0*(double)m.daily[i]/(double)peak*sceneProgress);
    if(h>0) {box(x,148-h,19,h,accent,2);box(x,148-h,19,min(h,3),UI_TEXT,1);}else box(x,147,19,1,UI_LINE);
    label(String(m.days[i]).length()>=10?String(m.days[i]+8):"--",x+2,151,UI_DIM);
  }
  else label("每日统计未返回",96,130,UI_DIM);
  box(10,174,300,31,UI_CARD,7);
  label("缓存命中",20,180,UI_DIM);label(m.hitAvailable?String(m.hit,1)+"%":"--",92,179,UI_AMBER,16);
  progress(161,185,131,6,m.hitAvailable?m.hit/100:0,UI_AMBER);
  label(metricSource?"会话开始日归属":"账户日统计有更新延迟",85,111,UI_DIM);
}
void sourcePage() {
  for(int i=0;i<2;i++) {
    int y=43+i*69;uint16_t accent=i?UI_GREEN:UI_CYAN;
    box(10,y,300,62,UI_CARD,9);dot(30,y+21,10,UI_SELECTED);label(i?"H":"C",25,y+13,accent,16);
    label(i?"Hermes":"Codex",49,y+8,UI_TEXT,16);
    bool online=view.online[i];dot(232,y+16,3,online?UI_GREEN:UI_RED);label(online?"在线":"离线",242,y+9,online?UI_GREEN:UI_RED);
    label(view.live[i]?"实时同步":view.healthy[i]?"历史数据":"连接异常",49,y+29,accent);
    label(fitted(view.detail[i][0]?String(view.detail[i]):String(i?"桌面 / CLI / Gateway":"本机 Desktop IPC / SQLite"),274),20,y+46,UI_DIM);
  }
  box(10,183,300,22,UI_SELECTED,6);wifiIcon(24,190,WiFi.status()==WL_CONNECTED?UI_CYAN:UI_RED);
  label("Wi-Fi",40,187,UI_DIM);label(WiFi.localIP().toString(),184,187,UI_TEXT);
}
String byteRate(float n) {
  return n>=1048576?String(n/1048576,1)+"M/s":n>=1024?String(n/1024,1)+"K/s":String(n,0)+"B/s";
}
float hostValue(const HostSample& sample,int channel) {
  return channel==0?sample.cpu:channel==1?sample.memory:channel==2?sample.rx:sample.tx;
}
void hostTrend(int channel,float maximum,uint16_t c,uint32_t newest) {
  int previousX=-1,previousY=0;uint32_t previousAt=0;
  for(int i=0;i<view.host.count;i++) {
    const HostSample& sample=view.host.history[i];
    int64_t age=(int64_t)newest-sample.at;
    if(age<0 || age>118 || !(sample.valid & (1<<channel))) {previousX=-1;continue;}
    int x=40+(118-age)*254/118;
    int y=160-(int)(constrain(hostValue(sample,channel)/maximum,0.0f,1.0f)*43*sceneProgress);
    if(previousX>=0 && sample.at>=previousAt && sample.at-previousAt<=5) {
      segment(previousX,previousY,x,y,c);segment(previousX,previousY+1,x,y+1,c);
    }
    previousX=x;previousY=y;previousAt=sample.at;
  }
  if(previousX>=0) dot(previousX,previousY,2,c);
}
void hostPage() {
  HostMetric& h=view.host;
  if(!h.received) {emptyState("正在获取主机状态","采样曲线每两秒更新",false);return;}
  if(!h.available) {emptyState("主机采样不可用",h.detail[0]?String(h.detail):String("请更新本机监视服务"),false);return;}
  box(10,41,146,50,UI_CARD,8);box(164,41,146,50,UI_CARD,8);
  label(hostMode?"下载 / RX":"CPU",22,46,UI_DIM);label(hostMode?"上传 / TX":"内存",176,46,UI_DIM);
  label(hostMode?(h.networkReady?byteRate(h.rx):"--"):h.cpuReady?String(h.cpu,1)+"%":"--",22,61,UI_CYAN,26);
  label(hostMode?(h.networkReady?byteRate(h.tx):"--"):String(h.memory,1)+"%",176,61,UI_GREEN,26);
  box(10,97,300,85,UI_CARD,9);
  dot(22,106,3,UI_CYAN);label(hostMode?"下载":"CPU",30,100,UI_CYAN);
  dot(80,106,3,UI_GREEN);label(hostMode?"上传":"内存",88,100,UI_GREEN);
  label(hostMode?fitted(String(h.interface),96):"近 2 分钟",212,100,UI_DIM);
  float maximum=100;
  if(hostMode) {
    maximum=1024;
    for(int i=0;i<h.count;i++) maximum=max(maximum,max(h.history[i].rx,h.history[i].tx)*1.1f);
  }
  for(int i=0;i<3;i++) {
    int y=117+i*21;segment(40,y,294,y,UI_LINE);
    String axis=hostMode?(maximum*(2-i)/2>=1048576?String(maximum*(2-i)/2097152,1)+"M":String(maximum*(2-i)/2048,0)+"K"):String(100-i*50);
    label(axis,14,y-5,UI_DIM);
  }
  label("-2分",40,166,UI_DIM);label("-1分",148,166,UI_DIM);label("现在",270,166,UI_DIM);
  if(h.count) {
    uint32_t newest=h.history[h.count-1].at;
    hostTrend(hostMode?2:0,maximum,UI_CYAN,newest);hostTrend(hostMode?3:1,maximum,UI_GREEN,newest);
  }
  if(hostMode && !h.networkReady) label("等待有效网卡速率采样",83,134,UI_AMBER);
  box(10,186,300,19,UI_SELECTED,5);
  if(hostMode) {
    label("运行 "+String(h.uptime/86400)+"天 "+String(h.uptime/3600%24)+"时",19,189,UI_DIM);
    label("负载 "+String(h.load,2)+" / "+String(h.cpuCount)+"核",176,189,UI_TEXT);
  } else {
    label("磁盘",19,189,UI_DIM);progress(52,192,61,5,h.disk/100,UI_AMBER);
    label(String(h.disk,0)+"%",119,189,UI_AMBER);
    label(String(h.memoryUsed,1)+" / "+String(h.memoryTotal,1)+"GiB",182,189,UI_DIM);
  }
}
void settingIcon(int which,int x,int y,uint16_t c) {
  if(which==0) {dot(x,y,4,c);for(int i=0;i<8;i++) {float a=i*PI/4;segment(x+cosf(a)*7,y+sinf(a)*7,x+cosf(a)*9,y+sinf(a)*9,c);}}
  else if(which==1) {box(x-7,y-3,4,6,c);segment(x-3,y-3,x+1,y-7,c);segment(x+1,y-7,x+1,y+7,c);segment(x+1,y+7,x-3,y+3,c);segment(x+5,y-4,x+7,y,c);segment(x+7,y,x+5,y+4,c);}
  else if(which==2) wifiIcon(x,y-4,c);
  else if(which==3) {dot(x,y,8,c,false);label("i",x-2,y-7,c);}
  else {segment(x+7,y,x-7,y,c);segment(x-7,y,x-2,y-5,c);segment(x-7,y,x-2,y+5,c);}
}
void settingsPage(const String& message) {
  const char* titles[]={"显示亮度","提示声音","重新配网","连接信息","返回监视"};
  for(int i=0;i<5;i++) {
    int y=41+i*28;bool chosen=setting==i;
    box(10,y,300,25,chosen?UI_SELECTED:UI_CARD,6);if(chosen) stroke(10,y,300,25,UI_CYAN,6);
    settingIcon(i,26,y+12,chosen?UI_CYAN:UI_DIM);label(titles[i],45,y+4,UI_TEXT,16);
    if(i==0) {progress(211,y+9,65,6,brightness/255.0,UI_CYAN);label(String((int)(brightness/255.0*100))+"%",280,y+6,UI_DIM);}
    if(i==1) {box(264,y+6,33,13,muted?UI_LINE:rgb(28,94,67),6);dot(muted?271:290,y+12,4,muted?UI_DIM:UI_GREEN);}
    if(i==2 || i==4) label(">",290,y+5,UI_DIM);
    if(i==3) label(WiFi.status()==WL_CONNECTED?"已连接":"未连接",250,y+6,UI_DIM);
  }
  label(setting==3?"IP "+WiFi.localIP().toString():setting==4?"按 B 返回进入设置前的页面":fitted(message,296),12,187,UI_DIM);

}
void portalPage(const String& message) {
  wifiIcon(28,54,UI_CYAN);label("手机连接临时热点",49,43,UI_TEXT,16);
  box(10,70,300,70,UI_CARD,9);label(apName,22,79,UI_CYAN,16);
  label("热点密码",22,106,UI_DIM);label(apPassword,102,102,UI_TEXT,16);
  box(10,148,300,29,UI_SELECTED,7);label("浏览器打开 192.168.4.1",22,154,UI_TEXT,16);
  wrap(message,14,187,292,1,UI_DIM);
}
void drawScene(const String& message,bool stale,uint32_t now) {
  sceneX=0;
  label("AI",10,7,UI_CYAN,16);label(apActive?"Wi-Fi 配网":pages[page],39,7,UI_TEXT,16);
  if(!apActive && page==0 && view.count) label(String(view.offset+selection[0]+1)+" / "+view.total,125,9,UI_DIM);
  if(apActive) pill("AP",244,5,UI_AMBER,36);
  else if(page!=Navigation::SETTINGS) for(int i=0;i<Navigation::TAB_COUNT;i++) box(181+i*13,15,i==page?9:4,4,i==page?UI_CYAN:UI_LINE,2);
  else pill("长 B 返回",206,5,UI_CYAN,74);
  wifiIcon(299,12,WiFi.status()!=WL_CONNECTED?UI_RED:(stale || message!="已连接")?UI_AMBER:UI_GREEN);
  segment(10,32,310,32,UI_LINE);
  sceneX=(int)((1-sceneProgress)*18)*transitionDirection;
  if(apActive) portalPage(message);
  else if(page==0) taskPage();else if(page==1) approvalPage(now);else if(page==2) metricPage();else if(page==3) sourcePage();else if(page==4) hostPage();else settingsPage(message);
  sceneX=0;
  segment(10,209,310,209,UI_LINE);
  bool toast=(int32_t)(controlToastUntil-now)>0;
  String age=view.received?String((now-view.received)/1000)+" 秒前":String("尚未同步");
  String status=toast?controlToast:message!="已连接"?message+" · "+(stale && view.received?String("数据已过期 ")+age:age):stale?"数据已过期 · "+age:"已同步 · "+age;
  label(fitted(status,294),12,211,toast?UI_AMBER:(stale || message!="已连接")?UI_RED:UI_DIM);
  if(page==Navigation::SETTINGS) {key("A","上一项",10);key("B",setting==4?"返回":"调整",113);key("C","下一项",218);}
  else {key("A",page==1?"页/长按拒绝":"上一页",10);key("B",page==0?"任务/长设置":page==1?"请求/长设置":page==2?"来源/长设置":page==4?"资源/网络":"长按设置",113);key("C",page==0?"页/长按详情":page==1?"页/长按同意":page==2?(metricMode?"页/长按图表":"页/长按额度"):"下一页",218);}
}
void drawFrame(const String& message,bool stale,uint32_t now) {
  float t=constrain((now-transitionAt)/240.0f,0.0f,1.0f);sceneProgress=1-powf(1-t,3);
  gfx=canvasReady?(lgfx::LGFXBase*)&canvas:(lgfx::LGFXBase*)&M5.Display;
  if(canvasReady) {
    for(stripY=0;stripY<240;stripY+=80) {
      canvas.fillScreen(UI_BG);drawScene(message,stale,now);
      const uint32_t* pixels=(const uint32_t*)canvas.getBuffer();uint32_t hash=2166136261UL;
      for(int i=0;i<320*80/2;i++) hash=(hash^pixels[i])*16777619UL;
      if(stripHashes[stripY/80]!=hash) {canvas.pushSprite(0,stripY);stripHashes[stripY/80]=hash;}
    }
  } else {stripY=0;M5.Display.fillScreen(UI_BG);drawScene(message,stale,now);}
  gfx=&M5.Display;stripY=0;sceneX=0;
}
