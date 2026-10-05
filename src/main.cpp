#include <M5Unified.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <esp_system.h>
#include <esp_log.h>
#include <ESPmDNS.h>
#include <type_traits>
#include "navigation.h"

// Only the network worker touches HTTP; only loop() touches display, NVS and buttons.
enum Status : uint8_t { IDLE, RUNNING, APPROVAL, WAIT_INPUT, DONE, FAILED, INTERRUPTED, UNKNOWN };
const char* states[] = {"idle","running","waiting_approval","waiting_input","completed","failed","interrupted","unknown"};
const char* labels[] = {"空闲","运行中","等待审批","等待输入","已完成","失败","已中断","未知"};
const char* pages[] = {"运行列表","待审批","用量图表","来源","设置"};
struct Task {
  char id[96]{}, title[193]{}, summary[289]{}, action[145]{}, kind[25]{};
  Status status = UNKNOWN;
  uint32_t started=0, ended=0, updated=0;
  bool waitSupported=false;
};
struct Event { char id[24]{}, title[193]{}, source[8]{}; Status status=UNKNOWN; uint32_t at=0; };
struct Approval { char id[25]{}, title[193]{}, command[769]{}, source[8]{}; bool canApprove=false, canDeny=false; };
struct Quota { char label[33]{}; float used=0; uint32_t reset=0, minutes=0; };
struct Metric {
  bool available=false, hitAvailable=false, dailyAvailable=false, quotaAvailable=false;
  uint64_t input=0, output=0, cached=0, total=0, daily[7]{};
  char days[7][11]{};
  float hit=0;
  int quotaCount=0;
  Quota quotas[4];
};
struct Snapshot {
  Task tasks[10]; Event events[20]; Approval approvals[10]; Metric metrics[2];
  int approvalCount=0;
  int count=0, eventCount=0, total=0, offset=0, counts[8]{};
  bool online[2]{}, healthy[2]{}, live[2]{};
  char detail[2][145]{}, cursor[160]{};
  uint32_t received=0, observed=0, generated=0, sequence=0;
};
static_assert(std::is_trivially_copyable<Snapshot>::value, "Snapshot reset requires plain data");
void resetSnapshot(Snapshot& out) {
  // Assignment from Snapshot{} creates a 13KB temporary on the ESP32 stack.
  // Clear the existing heap object directly instead.
  memset(&out,0,sizeof(out));
  for(auto& task:out.tasks) task.status=UNKNOWN;
  for(auto& event:out.events) event.status=UNKNOWN;
}
struct Config { String ssid, password, url, token; };
Preferences prefs;
WebServer portal(80);
SemaphoreHandle_t guard;
Snapshot cache[1], view;
// Reserve the parse arena once, before Wi-Fi/speaker/display allocations.
// A later 32KB per-request allocation can fail on a fragmented non-PSRAM heap.
DynamicJsonDocument networkJson(32768);
Snapshot& overview=view;
Config activeConfig, candidateConfig;
bool candidatePending=false, candidateValid=false, apActive=false, closePortal=false;
String netMessage="正在启动", requestedCursor[3], apName, apPassword;
int requestedSource=0;
uint32_t epochBase=0, epochMillis=0;
Navigation navigation;
int& page=navigation.page;int& setting=navigation.setting;
int selection[4]{};
String selectedId[3];
bool muted=false, bLong=false, cLong=false, aLong=false, acLong=false, acSuppress=false, dirty=true;
int textPage=0;
uint8_t brightness=128;
String oldBody;
uint32_t acPressed=0, lastRender=0, lastButton=0, alertsSequence=0;
bool alertsInitialized=false;
int beepsRemaining=0; uint32_t nextBeep=0;
uint32_t networkStackFree=0;
String pendingApprovalId, pendingApprovalChoice, controlResult, controlToast;
uint32_t controlToastUntil=0;
bool controlPending=false;
int metricSource=0, metricMode=0;
String selectedApprovalId;
M5Canvas canvas(&M5.Display);
bool canvasReady=false;
uint32_t transitionAt=0, frameMicros=0, aPressedAt=0, cPressedAt=0;
int transitionDirection=1;

void lock() { xSemaphoreTake(guard, portMAX_DELAY); }
void unlock() { xSemaphoreGive(guard); }
Status parseStatus(const char* value) {
  for (int i=0;i<8;i++) if (!strcmp(value ? value : "", states[i])) return (Status)i;
  return UNKNOWN;
}
template<size_t N> void copyText(char (&dst)[N], const char* src) {
  if (!src) src="";
  size_t n = strnlen(src, N-1);
  // Never cut inside a UTF-8 codepoint.
  if (n == N-1 && src[n]) while(n && (((uint8_t)src[n] & 0xC0) == 0x80)) --n;
  memcpy(dst, src, n); dst[n]=0;
}
uint32_t nowEpoch() { return epochBase ? epochBase+(millis()-epochMillis)/1000 : 0; }
uint16_t color(Status s) {
  if(s==RUNNING) return 0x05FF;
  if(s==APPROVAL || s==WAIT_INPUT) return 0xFE80;
  if(s==DONE) return 0x07E0;
  if(s==FAILED) return 0xF800;
  return 0xAD55;
}
String elapsed(uint32_t start, uint32_t end=0) {
  uint32_t now=end ? end : nowEpoch();
  uint32_t seconds=(start && now>=start) ? now-start : 0;
  return String(seconds/3600)+":"+(seconds/60%60<10?"0":"")+String(seconds/60%60)+":"+(seconds%60<10?"0":"")+String(seconds%60);
}
bool sameConfig(const Config& a,const Config& b) { return a.ssid==b.ssid && a.password==b.password && a.url==b.url && a.token==b.token; }
void saveConfig(const Config& c) {
  DynamicJsonDocument doc(1536);
  doc["ssid"]=c.ssid; doc["password"]=c.password; doc["url"]=c.url; doc["token"]=c.token;
  String json; serializeJson(doc,json); prefs.putString("network",json);
}
bool readConfig(Config& c) {
  DynamicJsonDocument doc(1536);
  if(deserializeJson(doc,prefs.getString("network",""))) return false;
  c.ssid=doc["ssid"].as<String>(); c.password=doc["password"].as<String>();
  c.url=doc["url"].as<String>(); c.token=doc["token"].as<String>();
  return c.ssid.length() && c.url.length() && c.token.length();
}
void setMessage(const String& value) { lock(); netMessage=value; unlock(); }

bool fetch(const Config& cfg,int source,const String& cursor,Snapshot& out,String& error) {
  WiFiClient client;
  HTTPClient http;
  String base=cfg.url;
  int hostEnd=base.indexOf('/',7);if(hostEnd<0) hostEnd=base.length();
  int portAt=base.indexOf(':',7);if(portAt>=0 && portAt<hostEnd) hostEnd=portAt;
  String host=base.substring(7,hostEnd);
  if(host.endsWith(".local")) {
    static bool mdnsReady=false;
    if(!mdnsReady) mdnsReady=MDNS.begin("ai-monitor-"+String((uint32_t)ESP.getEfuseMac(),HEX));
    IPAddress address;
    if(mdnsReady) address=MDNS.queryHost(host.substring(0,host.length()-6),1500);
    if(!address) {error="服务域名未找到，请使用 IP";return false;}
    base="http://"+address.toString()+base.substring(hostEnd);
  }
  String url=base+"/api/v1/snapshot?view=active&source="+(source==1?"codex":source==2?"hermes":"all");
  if(source==0) url+="&device="+String((uint32_t)ESP.getEfuseMac(),HEX)+"&uptime="+String(millis()/1000)+"&heap="+String(ESP.getFreeHeap())+"&min_heap="+String(ESP.getMinFreeHeap());
  if(cursor.length()) url+="&cursor="+cursor;
  http.setConnectTimeout(2000); http.setTimeout(2500);
  if(!http.begin(client,url)) {error="服务地址无效";return false;}
  http.addHeader("Authorization","Bearer "+cfg.token);
  int code=http.GET();
  if(code!=200) {
    error=code==401?"认证失败：检查 Token":code<0?"服务不可达":"服务错误 "+String(code);
    http.end();return false;
  }
  int length=http.getSize();
  if(length<=0 || length>16384) {error="数据大小不兼容";http.end();return false;}
  String payload=http.getString();http.end();
  if(payload.length()!=(size_t)length) {error="响应不完整";return false;}
  auto& doc=networkJson;
  auto decodeError=deserializeJson(doc,payload);
  if(decodeError) {error="数据解析失败: "+String(decodeError.c_str());return false;}
  if(doc["version"].as<int>()!=1 || !doc["tasks"].is<JsonArray>() || !doc["events"].is<JsonArray>() || !doc["sources"].is<JsonObject>()) {
    error="数据格式不兼容";return false;
  }
  if(doc["tasks"].size()>10 || doc["events"].size()>20) {error="数据数量不兼容";return false;}
  resetSnapshot(out);
  out.total=doc["total"]|0; out.offset=doc["offset"]|0;
  out.generated=doc["generated_at"]|0U;out.observed=doc["observed_at"]|0U;
  out.sequence=doc["event_sequence"]|0U;
  for(int i=0;i<8;i++) out.counts[i]=doc["counts"][states[i]]|0;
  for(int i=0;i<2;i++) {
    JsonObject h=doc["sources"][i==0?"codex":"hermes"];
    out.online[i]=h["online"]|false;out.healthy[i]=h["healthy"]|false;out.live[i]=h["live"]|false;
    copyText(out.detail[i],h["detail"]|"");
  }
  copyText(out.cursor,doc["next_cursor"]|"");
  for(JsonObject t:doc["tasks"].as<JsonArray>()) {
    Task& x=out.tasks[out.count++];
    copyText(x.id,t["id"]|"");copyText(x.title,t["title"]|"");copyText(x.summary,t["summary"]|"");
    copyText(x.action,t["action"]|"");copyText(x.kind,t["kind"]|"");
    x.status=parseStatus(t["status"]|"unknown");x.started=t["started_at"]|0U;
    x.ended=t["ended_at"]|0U;x.updated=t["updated_at"]|0U;x.waitSupported=t["wait_supported"]|false;
  }
  for(JsonObject e:doc["events"].as<JsonArray>()) {
    Event& x=out.events[out.eventCount++];copyText(x.id,e["id"]|"");copyText(x.title,e["title"]|"");
    copyText(x.source,e["source"]|"");x.status=parseStatus(e["status"]|"unknown");x.at=e["at"]|0U;
  }
  for(JsonObject a:doc["approvals"].as<JsonArray>()) {
    if(out.approvalCount>=10) break;
    Approval& x=out.approvals[out.approvalCount++];
    copyText(x.id,a["id"]|"");copyText(x.title,a["title"]|"");copyText(x.command,a["command"]|"");copyText(x.source,a["source"]|"");
    x.canApprove=a["can_approve"]|false;x.canDeny=a["can_deny"]|false;
  }
  for(int i=0;i<2;i++) {
    JsonObject m=doc["metrics"][i==0?"codex":"hermes"];Metric& x=out.metrics[i];
    x.available=m["available"]|false;x.input=m["input"]|uint64_t(0);x.output=m["output"]|uint64_t(0);x.cached=m["cached"]|uint64_t(0);x.total=m["total"]|uint64_t(0);
    x.hitAvailable=!m["hit_percent"].isNull();x.hit=m["hit_percent"]|0.0f;
    x.dailyAvailable=m["daily"].is<JsonArray>();int d=0;
    for(JsonObject day:m["daily"].as<JsonArray>()) {if(d>=7) break;x.daily[d]=day["tokens"]|uint64_t(0);copyText(x.days[d],day["day"]|"");d++;}
    JsonObject q=m["quota"];x.quotaAvailable=q["available"]|false;
    for(JsonObject w:q["windows"].as<JsonArray>()) {
      if(x.quotaCount>=4) break;Quota& row=x.quotas[x.quotaCount++];copyText(row.label,w["label"]|"");row.used=w["used_percent"]|0.0f;row.reset=w["reset_at"]|0U;row.minutes=w["duration_mins"]|0U;
    }
  }
  out.received=millis();error="";return true;
}

void networkWorker(void*) {
  Config working;
  uint32_t retryAt=0, backoff=1000, wifiBegan=0;
  bool trying=false, testing=false;
  String candidateError;
  // Large snapshots live on the heap, not the worker stack.
  Snapshot* next=new Snapshot;
  for(;;) {
    lock();
    bool pending=candidatePending;
    if(pending) {working=candidateConfig;candidatePending=false;testing=true;}
    else if(!testing) working=activeConfig;
    String cursor=requestedCursor[0];
    bool act=controlPending;String approvalId=pendingApprovalId,choice=pendingApprovalChoice;
    unlock();
    if(pending) {candidateError="";WiFi.disconnect(false);trying=false;retryAt=millis();backoff=1000;}
    if(!working.ssid.length()) {setMessage(candidateError.length()?candidateError:"请连接设备热点配网");vTaskDelay(pdMS_TO_TICKS(200));continue;}
    if(WiFi.status()!=WL_CONNECTED) {
      uint32_t now=millis();
      if(!trying && (int32_t)(now-retryAt)>=0) {
        WiFi.begin(working.ssid.c_str(),working.password.c_str());wifiBegan=now;trying=true;
        setMessage(testing?"正在验证新 Wi-Fi":"正在连接 Wi-Fi");
      }
      if(trying && now-wifiBegan>15000) {
        WiFi.disconnect(false);trying=false;retryAt=now+backoff;backoff=min(backoff*2,30000U);
        if(testing) {testing=false;lock();working=activeConfig;unlock();candidateError="新 Wi-Fi 失败，请返回修改";setMessage(candidateError);}
        else setMessage("Wi-Fi 断开，自动重连");
      }
      vTaskDelay(pdMS_TO_TICKS(100));continue;
    }
    trying=false;
    if(!act && (int32_t)(millis()-retryAt)<0) {vTaskDelay(pdMS_TO_TICKS(100));continue;}
    String error;
    if(act) {
      lock();controlPending=false;unlock();
      WiFiClient client;HTTPClient http;String result;
      http.setConnectTimeout(2000);http.setTimeout(2500);
      if(http.begin(client,working.url+"/api/v1/approvals/respond")) {
        http.addHeader("Authorization","Bearer "+working.token);http.addHeader("Content-Type","application/json");
        int code=http.POST("{\"id\":\""+approvalId+"\",\"choice\":\""+choice+"\"}");
        result=code==200?(choice=="once"?"已批准这一次":"已拒绝"):code==409?"请求已失效，请刷新":code==401?"认证失败":"审批未送达，请重试";http.end();
      } else result="审批服务地址无效";
      lock();controlResult=result;unlock();
    }
    bool ok=fetch(working,0,cursor,*next,error);
    lock();networkStackFree=uxTaskGetStackHighWaterMark(nullptr);unlock();
    if(ok) {
      lock();cache[0]=*next;
      if(testing) {activeConfig=working;candidateValid=true;testing=false;closePortal=true;}
      netMessage="已连接";unlock();
      backoff=1000;retryAt=millis()+2000;
    } else {
      setMessage(error);retryAt=millis()+backoff;backoff=min(backoff*2,30000U);
      if(testing) {candidateError=error;testing=false;WiFi.disconnect(false);retryAt=millis()+3000;}
    }
    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

const char portalHtml[] PROGMEM=R"HTML(<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Monitor 配网</title><style>body{font:16px system-ui;background:#101827;color:#e6ecff;max-width:480px;margin:40px auto;padding:20px}input,button{box-sizing:border-box;width:100%;padding:12px;margin:8px 0 18px;border-radius:8px;border:1px solid #64748b}button{background:#38bdf8;font-weight:bold}small{color:#a5b4cc}</style><h1>AI Monitor 配网</h1><p>填写本机 2.4GHz Wi-Fi 和监视服务信息。</p><form method="post" action="/configure"><label>Wi-Fi 名称<input name="ssid" maxlength="32" required></label><label>Wi-Fi 密码<input name="password" type="password" maxlength="63"></label><label>服务地址<input name="url" placeholder="http://192.168.1.10:8766" maxlength="160" required></label><label>监视 Token<input name="token" type="password" maxlength="128" required></label><button>连接并验证</button></form><small>验证成功才保存配置并关闭热点。失败时保留旧配置。</small></html>)HTML";

void startPortal() {
  if(apActive) return;
  WiFi.mode(WIFI_AP_STA);
  uint8_t bytes[6];esp_fill_random(bytes,6);
  char pass[13];for(int i=0;i<6;i++) snprintf(pass+i*2,3,"%02x",bytes[i]);
  apPassword=pass;apName="AI-Monitor-"+String((uint16_t)(ESP.getEfuseMac()>>32),HEX);
  if(!WiFi.softAP(apName.c_str(),apPassword.c_str())) {setMessage("热点启动失败");return;}
  portal.on("/",HTTP_GET,[]{portal.send_P(200,"text/html; charset=utf-8",portalHtml);});
  portal.on("/status",HTTP_GET,[]{lock();String message=netMessage;unlock();portal.send(200,"text/plain; charset=utf-8",message);});
  portal.on("/configure",HTTP_POST,[]{
    Config next;next.ssid=portal.arg("ssid");next.password=portal.arg("password");next.url=portal.arg("url");next.token=portal.arg("token");
    next.url.trim();next.token.trim();while(next.url.endsWith("/")) next.url.remove(next.url.length()-1);
    if(next.ssid.length()<1 || next.ssid.length()>32 || next.password.length()>63 || next.token.length()<16 || next.token.length()>128 || !next.url.startsWith("http://") || next.url.length()>160 || next.url.indexOf('@')>=0 || next.url.indexOf('?')>=0 || next.url.indexOf('#')>=0) {
      portal.send(400,"text/plain; charset=utf-8","配置无效：填写 2.4GHz Wi-Fi、http://服务地址与完整 Token。");return;
    }
    lock();candidateConfig=next;candidatePending=true;unlock();
    portal.send(200,"text/html; charset=utf-8","<meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><h2>正在连接并验证</h2><p>成功后设备退出热点；失败可返回修改，旧配置保持不变。</p><p><a href='/status'>查看连接结果</a> · <a href='/'>返回配网</a></p>");
  });
  portal.onNotFound([]{portal.sendHeader("Location","/",true);portal.send(302,"text/plain","");});
  portal.begin();apActive=true;dirty=true;
}
void changePage(int next) {
  transitionDirection=next<page?-1:1;navigation.move(next);
  transitionAt=millis();textPage=0;dirty=true;
  lock();requestedSource=0;unlock();
}

void toggleSettings() {
  navigation.toggleSettings();transitionAt=millis();textPage=0;dirty=true;
}
void leaveSettings() {
  navigation.leaveSettings();transitionAt=millis();textPage=0;dirty=true;
}
void adjustSetting() {
  if(setting==0) {brightness=brightness>=224?64:brightness+32;M5.Display.setBrightness(brightness);prefs.putUChar("brightness",brightness);}
  else if(setting==1) {muted=!muted;prefs.putBool("muted",muted);if(muted) {beepsRemaining=0;M5.Speaker.stop();}}
  else if(setting==2) startPortal();
  else if(setting==4) leaveSettings();
  dirty=true;
}

void selectNext() {
  textPage=0;
  if(page==0) {
    if(selection[0]+1<view.count) {selection[0]++;selectedId[0]=view.tasks[selection[0]].id;}
    else if(!view.cursor[0] && view.total<=view.count) {selection[0]=0;if(view.count) selectedId[0]=view.tasks[0].id;}
    else {lock();requestedCursor[0]=String(view.cursor);cache[0].count=0;cache[0].received=0;unlock();selection[0]=0;selectedId[0]="";}
  } else if(page==1 && view.approvalCount) {selection[1]=(selection[1]+1)%view.approvalCount;selectedApprovalId=view.approvals[selection[1]].id;}
  else if(page==2) metricSource=(metricSource+1)%2;
  dirty=true;
}
String compactTokens(uint64_t n) {
  return n>=1000000000ULL?String((double)n/1000000000,2)+"B":n>=1000000?String((double)n/1000000,2)+"M":n>=1000?String((double)n/1000,1)+"K":String((uint32_t)n);
}
bool commandFits(const char* command) {
  M5.Display.setFont(&fonts::efontCN_12);
  String line;int lines=1;String value=command;
  for(size_t i=0;i<value.length();) {
    uint8_t ch=value[i];int n=ch<0x80?1:(ch&0xE0)==0xC0?2:(ch&0xF0)==0xE0?3:4;
    String piece=value.substring(i,i+n);i+=n;
    if(piece=="\n" || M5.Display.textWidth(line+piece)>272) {line="";lines++;}
    if(piece!="\n") line+=piece;
  }
  return value.length()>0 && lines<=7;
}
void queueApproval(bool accept) {
  if(WiFi.status()!=WL_CONNECTED || apActive || !view.received || millis()-view.received>10000) {
    controlToast="连接已过期，请先恢复同步";controlToastUntil=millis()+5000;dirty=true;return;
  }
  if(!view.approvalCount || selection[1]>=view.approvalCount) return;
  Approval& a=view.approvals[selection[1]];
  if((accept && (!a.canApprove || !commandFits(a.command))) || (!accept && !a.canDeny)) {
    controlToast="请在电脑查看并处理此请求";controlToastUntil=millis()+5000;dirty=true;return;
  }
  lock();if(!controlPending) {pendingApprovalId=a.id;pendingApprovalChoice=accept?"once":"deny";controlPending=true;}unlock();
  controlToast="正在提交审批决定";controlToastUntil=millis()+5000;dirty=true;
}

#include "ui.h"
void render() {
  lock();view=cache[0];String message=netMessage;
  if(controlResult.length()) {controlToast=controlResult;controlResult="";controlToastUntil=millis()+5000;}
  bool save=candidateValid;Config config=activeConfig;candidateValid=false;
  bool close=closePortal;closePortal=false;unlock();
  if(save) saveConfig(config);
  if(close && apActive) {portal.stop();WiFi.softAPdisconnect(true);WiFi.mode(WIFI_STA);apActive=false;dirty=true;}
  if(overview.received) {epochBase=overview.generated;epochMillis=overview.received;}
  uint32_t now=millis();
  uint32_t displayReceived=view.received;
  bool stale=!displayReceived || now-displayReceived>10000 || (view.generated>view.observed && view.generated-view.observed>10);
  if(page==0) {
    bool found=false;
    for(int i=0;i<view.count;i++) if(selectedId[0]==view.tasks[i].id) {selection[0]=i;found=true;break;}
    if(!found || selection[0]>=view.count) selection[0]=0;
    if(view.count) selectedId[0]=view.tasks[selection[0]].id;
  }
  if(page==1) {
    bool found=false;
    for(int i=0;i<view.approvalCount;i++) if(selectedApprovalId==view.approvals[i].id) {selection[1]=i;found=true;break;}
    if(!found || selection[1]>=view.approvalCount) selection[1]=0;
    if(view.approvalCount) selectedApprovalId=view.approvals[selection[1]].id;
  }
  String fingerprint=String(page)+"|"+message+"|"+String(stale)+"|"+String(displayReceived)+"|"+String(now/1000)+"|"+String(setting)+"|"+String(brightness)+"|"+String(muted)+"|"+String(metricSource)+"|"+String(metricMode)+"|"+String(selection[0])+"|"+String(selection[1])+"|"+String(textPage)+controlToast;
  bool holding=page==1 && !acSuppress && (M5.BtnA.isPressed() || M5.BtnC.isPressed());
  bool animating=now-transitionAt<240;
  if(dirty || oldBody!=fingerprint || animating || sceneProgress<1 || holding) {
    uint32_t began=micros();
    drawFrame(message,stale,now);
    frameMicros=micros()-began;oldBody=fingerprint;
  }
  dirty=false;
  // Baseline the current event sequence once per boot; no historical sound replay.
  if(!alertsInitialized && overview.received) {alertsSequence=overview.sequence;alertsInitialized=true;}
  else if(alertsInitialized && overview.sequence> alertsSequence) {
    for(int i=overview.eventCount-1;i>=0;i--) {
      Event& e=overview.events[i];uint32_t id=strtoul(e.id,nullptr,10);
      if(id<=alertsSequence) continue;
      int count=e.status==FAILED?3:(e.status==APPROVAL || e.status==WAIT_INPUT)?2:e.status==DONE?1:0;
      if(!muted) beepsRemaining=min(beepsRemaining+count,6);
    }
    alertsSequence=overview.sequence;
  }
}
void serialDiagnostics() {
  static String line;
  while(Serial.available()) {
    char c=Serial.read();
    if(c!='\n') {if(line.length()<32) line+=c;continue;}
    line.trim();
    if(line=="INFO") {
      lock();String message=netMessage;uint32_t received=cache[0].received;int total=cache[0].total;uint32_t stackFree=networkStackFree;unlock();
      Serial.printf("INFO {\"uptime\":%lu,\"heap\":%u,\"min_heap\":%u,\"max_heap_block\":%u,\"network_stack_free\":%lu,\"wifi\":%d,\"ap\":%s,\"page\":%d,\"tasks\":%d,\"age_ms\":%lu,\"message\":\"%s\"}\n",millis()/1000,ESP.getFreeHeap(),ESP.getMinFreeHeap(),ESP.getMaxAllocHeap(),stackFree,WiFi.status(),apActive?"true":"false",page,total,received?millis()-received:0,message.c_str());
    } else if(line.startsWith("TAB ")) {changePage(line.substring(4).toInt());render();Serial.printf("UI page=%d setting=%d\n",page,setting);}
    else if(line=="SETTINGS") {if(page!=4) toggleSettings();render();Serial.printf("UI page=%d setting=%d\n",page,setting);}
    else if(line=="BACK") {if(page==4) leaveSettings();render();Serial.printf("UI page=%d setting=%d\n",page,setting);}
    else if(line=="NEXT_SETTING") {if(page==4) navigation.selectSetting(1);dirty=true;render();Serial.printf("UI page=%d setting=%d\n",page,setting);}
    else if(line=="SELECT_BACK") {if(page==4) {setting=4;adjustSetting();}render();Serial.printf("UI page=%d setting=%d\n",page,setting);}
    else if(line=="METRIC") {metricSource=(metricSource+1)%2;dirty=true;render();Serial.println("UI metric switched");}
    else if(line=="MODE") {metricMode=(metricMode+1)%2;dirty=true;render();Serial.println("UI metric mode switched");}
    else if(line=="DETAIL") {textPage=!textPage;dirty=true;render();Serial.println("UI detail switched");}
    else if(line=="PERF") {Serial.printf("PERF {\"strip_buffer\":%s,\"frame_us\":%lu}\n",canvasReady?"true":"false",frameMicros);}
    else if(line=="TEST_SOUND") {if(!muted) beepsRemaining=3;} else if(line=="SCREEN") {
      // Explicit diagnostic only: capture current LCD pixels, no configuration or credentials.
      Serial.println("RGB 320 240");uint8_t row[320*3];
      for(int y=0;y<240;y++) {M5.Display.readRectRGB(0,y,320,1,row);Serial.write(row,sizeof(row));}
      Serial.println();
    }
    line="";
  }
}
void setup() {
  auto cfg=M5.config();cfg.clear_display=true;M5.begin(cfg);
  Serial.begin(115200);Serial.setDebugOutput(false);esp_log_level_set("*",ESP_LOG_NONE);
  // Arduino 2.0.16 log_printf spins until TX is idle. A long SCREEN stream
  // keeps TX busy and can starve IDLE0 when WiFiClient logs an error.
  // Only our structured diagnostic replies use UART; errors remain on the LCD.
  Serial.println("AI Monitor boot v3 UI");
  M5.Display.setRotation(1);M5.Display.fillScreen(UI_BG);
  // 51KB RGB565 strip, instead of a 154KB full-frame allocation on a non-PSRAM Core.
  canvas.setColorDepth(16);canvas.setPsram(false);
  canvasReady=canvas.createSprite(320,80)!=nullptr;
  prefs.begin("ai-monitor",false);
  brightness=prefs.getUChar("brightness",128);muted=prefs.getBool("muted",false);
  M5.Display.setBrightness(brightness);M5.Speaker.setVolume(32);
  guard=xSemaphoreCreateMutex();readConfig(activeConfig);
  WiFi.mode(WIFI_STA);WiFi.setAutoReconnect(false);
  if(!activeConfig.ssid.length()) startPortal();
  xTaskCreatePinnedToCore(networkWorker,"monitor-network",8192,nullptr,1,nullptr,0);
  lastButton=millis();render();
}
void loop() {
  M5.update();uint32_t now=millis();
  if(M5.BtnA.wasPressed()) aPressedAt=now;
  if(M5.BtnC.wasPressed()) cPressedAt=now;
  if(apActive) portal.handleClient();
  if(M5.BtnA.isPressed() && M5.BtnC.isPressed()) {
    if(!acPressed) acPressed=now;acSuppress=true;
    if(now-acPressed>=5000 && !acLong) {acLong=true;startPortal();}
  } else {acPressed=0;acLong=false;}
  if(!acSuppress && page==1) {
    if(M5.BtnC.pressedFor(2000) && !cLong) {cLong=true;queueApproval(true);}
    if(M5.BtnA.pressedFor(2000) && !aLong) {aLong=true;queueApproval(false);}
  } else if(!acSuppress && (page==0 || page==2) && M5.BtnC.pressedFor(800) && !cLong) {cLong=true;if(page==0) textPage=(textPage+1)%2;else metricMode=(metricMode+1)%2;dirty=true;}
  if(M5.BtnB.pressedFor(2000) && !bLong) {
    bLong=true;
    toggleSettings();
  }
  if(M5.BtnB.wasReleased()) {
    if(!bLong) {
      if(page==4) {
        adjustSetting();
      } else selectNext();
    }
    bLong=false;lastButton=now;
  }
  if(!M5.BtnA.isPressed() || !M5.BtnC.isPressed()) {
    if(M5.BtnA.wasClicked() && !acSuppress && !aLong) {if(page==4) navigation.selectSetting(-1);else changePage(page-1);dirty=true;}
    if(M5.BtnC.wasClicked() && !acSuppress && !cLong) {if(page==4) navigation.selectSetting(1);else changePage(page+1);dirty=true;}
  }
  if(M5.BtnC.wasReleased()) cLong=false;
  if(M5.BtnA.wasReleased()) aLong=false;
  if(!M5.BtnA.isPressed() && !M5.BtnC.isPressed()) acSuppress=false;
  if(beepsRemaining>0 && (int32_t)(now-nextBeep)>=0) {M5.Speaker.tone(1800,100);beepsRemaining--;nextBeep=now+220;}
  if(dirty || now-lastRender>=50) {render();lastRender=now;}
  serialDiagnostics();delay(5);
}
