# -*- coding: utf-8 -*-
"""
KOOK 语音频道聊天框自动回复脚本（CDP 附加方案）

原理：
- 通过 Chrome DevTools Protocol (CDP) 附加到正在运行的 KOOK 桌面客户端
- 注入 MutationObserver 监听语音频道消息列表 DOM，从 React fiber 提取消息（uid/content）
- 按 config.yaml 规则匹配（原样字符 contains、白名单、冷却、频控）
- 通过 CDP 模拟输入 + 回车发送回复（以本人账号身份）

前置条件：
- KOOK 以调试模式启动：KOOK.exe --remote-debugging-port=9222 --remote-allow-origins=*
- 本账号已登录并挂在该语音频道内
"""

import json
import os
import time
import yaml
import websocket

CDP_HTTP = "http://127.0.0.1:9222"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

# ---------------------------------------------------------------------------
# 注入到 KOOK 页面的 JS：监听消息列表新增消息，从 React fiber 提取消息数据
# ---------------------------------------------------------------------------
INJECT_JS = r"""
(function(){
  if (window.__kookAutoReply) return 'already';
  window.__kookAutoReply = { queue: [], status: 'starting' };

  function extractMsg(item){
    try {
      var key = Object.keys(item).find(function(k){ return k.indexOf('__reactFiber$') === 0; });
      if (!key) return null;
      var fiber = item[key];
      for (var i = 0; i < 6 && fiber; i++){
        var p = fiber.memoizedProps;
        if (p && p.msgInfo && p.msgInfo.author){
          return {
            id: String(p.msgInfo.id || ''),
            uid: String(p.msgInfo.author.id || ''),
            name: String(p.msgInfo.author.username || ''),
            content: String(p.msgInfo.content || ''),
            ts: p.msgInfo.create_at || Date.now(),
            type: p.msgInfo.type || 0
          };
        }
        fiber = fiber.return;
      }
      return null;
    } catch(e){ return null; }
  }

  var container = document.querySelector('.voice-channel-message-list .text-message-container');
  if (!container){
    window.__kookAutoReply.status = 'container-not-found';
    return 'container-not-found';
  }

  var observer = new MutationObserver(function(muts){
    for (var m = 0; m < muts.length; m++){
      var added = muts[m].addedNodes;
      for (var n = 0; n < added.length; n++){
        var node = added[n];
        if (node.nodeType !== 1) continue;
        var items = [];
        if (node.classList && node.classList.contains('text-message-item')){
          items.push(node);
        } else if (node.querySelectorAll){
          items = node.querySelectorAll('.text-message-item');
        }
        for (var i = 0; i < items.length; i++){
          var info = extractMsg(items[i]);
          if (info) window.__kookAutoReply.queue.push(info);
        }
      }
    }
  });
  observer.observe(container, { childList: true, subtree: true });
  window.__kookAutoReply.status = 'ok';
  return 'hooked';
})()
"""

# 读取并清空消息队列的 JS
DRAIN_JS = r"""
(function(){
  var s = window.__kookAutoReply;
  if (!s) return null;
  var q = s.queue;
  s.queue = [];
  return JSON.stringify(q);
})()
"""

# 聚焦输入框的 JS
FOCUS_JS = r"""
(function(){
  var el = document.querySelector('.slate-editor');
  if (!el) return false;
  el.focus();
  el.click();
  return true;
})()
"""


def load_config(path=CONFIG_PATH):
    """加载配置文件"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_reply.log")


def log(msg):
    """写日志到 auto_reply.log（UTF-8，追加），同时打印到控制台"""
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class RuleMatcher:
    """
    纯逻辑匹配器：白名单 -> 关键词(原样字符 contains) -> 冷却 -> 频控。
    不依赖任何外部 IO，便于单元测试。
    """

    def __init__(self, config):
        self.config = config
        self.self_uid = str(config.get("account", {}).get("uid", ""))
        self.whitelist = set(str(u) for u in config.get("whitelist", {}).get("users", []))
        whitelist_mode = config.get("whitelist", {}).get("mode", "whitelist")
        self.is_whitelist_mode = whitelist_mode != "blacklist"
        self.rules = [r for r in config.get("rules", []) if r.get("enabled", True)]
        b = config.get("behavior", {})
        self.per_user_cooldown = float(b.get("per_user_cooldown", 0))
        self.global_rate = int(b.get("global_rate", 0))
        self.ignore_prefix = tuple(str(p) for p in b.get("ignore_prefix", []))
        self.ignore_bots = b.get("ignore_bots", True)
        self.ignore_system = b.get("ignore_system", True)
        self.quiet_hours = b.get("quiet_hours", [])
        # 状态
        self._last_user = {}          # uid -> 上次回复时间戳
        self._last_rule = {}          # rule name -> 上次回复时间戳
        self._global_times = []       # 全局频控滑动窗口
        self._last_replied = set()    # 消息 id 去重（每条消息只回一次）
        self._seq_idx = {}            # sequential 模式的轮换索引

    def _in_quiet_hours(self):
        if not self.quiet_hours:
            return False
        t = time.localtime()
        hm = t.tm_hour * 60 + t.tm_min
        for span in self.quiet_hours:
            try:
                start, end = span.split("-")
                sh, sm = map(int, start.split(":"))
                eh, em = map(int, end.split(":"))
                s = sh * 60 + sm
                e = eh * 60 + em
                if s <= e:
                    if s <= hm < e:
                        return True
                else:  # 跨天
                    if hm >= s or hm < e:
                        return True
            except Exception:
                continue
        return False

    def _check_global_rate(self):
        if self.global_rate <= 0:
            return True
        now = time.time()
        self._global_times = [x for x in self._global_times if now - x < 60]
        if len(self._global_times) >= self.global_rate:
            return False
        return True

    def _is_ignored_type(self, msg_type):
        """过滤系统/机器人/卡片类消息（按消息类型号判断）"""
        if self.ignore_system and msg_type in (2, 3, 4, 5, 6, 7, 8, 10, 11, 12):
            return True
        return False

    def _rule_allows(self, rule, msg):
        """规则级用户过滤：rule.users 为空 = 全部人可触发；
        否则按 用户ID / 用户名 / 用户名#识别码 任一命中即可"""
        users = rule.get("users") or []
        if not users:
            return True
        uid = str(msg.get("uid", ""))
        name = str(msg.get("name", ""))
        for u in users:
            u = str(u).strip()
            if not u:
                continue
            if u == uid:
                return True
            if u == name:
                return True
            if "#" in u and u.split("#", 1)[0] == name:
                return True
        return False

    def match(self, msg):
        """
        判断一条消息是否应回复，返回命中的 rule dict，否则返回 None。
        msg: {uid, content, id, type, ...}
        """
        if not msg:
            return None
        uid = str(msg.get("uid", ""))
        content = str(msg.get("content", ""))
        msg_id = str(msg.get("id", ""))
        msg_type = msg.get("type", 0)

        # 1. 自己发的消息不回（防死循环）
        if self.self_uid and uid == self.self_uid:
            return None
        # 2. 每条消息只回一次（去重）
        if msg_id in self._last_replied:
            return None
        # 3. 静默时段
        if self._in_quiet_hours():
            return None
        # 4. 消息类型过滤
        if self._is_ignored_type(msg_type):
            return None
        # 5. 前缀过滤
        if content.startswith(self.ignore_prefix):
            return None
        # 6. 白名单/黑名单
        if self.is_whitelist_mode:
            if self.whitelist and uid not in self.whitelist:
                return None
        else:
            if uid in self.whitelist:
                return None

        # 7. 关键词匹配（原样字符 contains）+ 规则级用户过滤，取 priority 最高
        hit = None
        for rule in self.rules:
            keywords = rule.get("keywords", [])
            if any(kw in content for kw in keywords) and self._rule_allows(rule, msg):
                if hit is None or rule.get("priority", 0) > hit.get("priority", 0):
                    hit = rule

        if hit is None:
            return None

        # 8. 同规则冷却
        rule_cooldown = float(hit.get("cooldown", 0))
        rule_key = hit.get("name", "")
        if rule_cooldown > 0:
            last = self._last_rule.get(rule_key, 0)
            if time.time() - last < rule_cooldown:
                return None

        # 9. 同一人冷却
        if self.per_user_cooldown > 0:
            last = self._last_user.get(uid, 0)
            if time.time() - last < self.per_user_cooldown:
                return None

        # 10. 全局频控
        if not self._check_global_rate():
            return None

        return hit

    def record_reply(self, msg, rule):
        """回复后记录状态（冷却、频控、去重）"""
        uid = str(msg.get("uid", ""))
        msg_id = str(msg.get("id", ""))
        now = time.time()
        self._last_user[uid] = now
        self._last_rule[rule.get("name", "")] = now
        self._global_times.append(now)
        self._last_replied.add(msg_id)
        # 控制去重集合大小
        if len(self._last_replied) > 5000:
            self._last_replied.clear()

    def pick_reply(self, rule):
        """按 reply_mode 选取回复文本"""
        replies = rule.get("replies", [])
        if not replies:
            return ""
        mode = rule.get("reply_mode", "fixed")
        if mode == "random":
            import random
            return random.choice(replies)
        if mode == "sequential":
            idx = self._seq_idx.get(rule.get("name", ""), 0)
            reply = replies[idx % len(replies)]
            self._seq_idx[rule.get("name", "")] = idx + 1
            return reply
        return replies[0]

class CDPClient:
    """CDP 客户端：连接 KOOK 页面、注入监听、轮询消息、模拟发送"""

    def __init__(self, http_base=CDP_HTTP, poll_interval=0.15):
        self.http_base = http_base
        self.poll_interval = poll_interval
        self.ws = None
        self.msg_id = 0

    def find_page(self):
        """从调试服务找到频道页面 target；优先精确匹配 config 里的目标频道"""
        import urllib.request
        with urllib.request.urlopen(self.http_base + "/json", timeout=5) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
        pages = [t for t in targets if t.get("type") == "page" and "/app/channels/" in t.get("url", "")]
        if not pages:
            return None
        cfg = load_config()
        want_sid = str(cfg.get("target", {}).get("server_id", ""))
        want_cid = str(cfg.get("target", {}).get("channel_id", ""))
        if want_sid and want_cid:
            for t in pages:
                if "/app/channels/%s/%s" % (want_sid, want_cid) in t.get("url", ""):
                    return t
        return pages[0]

    def connect(self):
        page = self.find_page()
        if not page:
            raise RuntimeError("未找到 KOOK 频道页面，请确认已登录并进入语音频道")
        self.ws = websocket.create_connection(
            page["webSocketDebuggerUrl"], timeout=10, origin="http://localhost:5888")
        # 验证连接
        r = self.evaluate("location.href")
        if not r:
            raise RuntimeError("CDP 连接失败")

    def cmd(self, method, params=None):
        self.msg_id += 1
        c = {"id": self.msg_id, "method": method}
        if params:
            c["params"] = params
        self.ws.send(json.dumps(c))
        while True:
            resp = json.loads(self.ws.recv())
            if resp.get("id") == self.msg_id:
                return resp

    def evaluate(self, expression):
        r = self.cmd("Runtime.evaluate", {
            "expression": expression, "returnByValue": True})
        result = r.get("result", {})
        if "exceptionDetails" in result:
            return None
        return result.get("result", {}).get("value")

    def inject(self):
        """注入消息监听"""
        return self.evaluate(INJECT_JS)

    def drain_queue(self):
        """读取并清空消息队列，返回消息列表"""
        raw = self.evaluate(DRAIN_JS)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

    def send_text(self, text):
        """模拟输入并回车发送消息"""
        if not self.evaluate(FOCUS_JS):
            return False
        time.sleep(0.05)
        self.cmd("Input.insertText", {"text": text})
        time.sleep(0.05)
        for typ in ("keyDown", "keyUp"):
            self.cmd("Input.dispatchKeyEvent", {
                "type": typ, "code": "Enter", "key": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        return True

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass


def main():
    log("=== 自动回复启动 ===")
    config = load_config()
    if not config.get("enable", True):
        log("config.enable = false，已停用，退出")
        return

    matcher = RuleMatcher(config)
    cdp = CDPClient()

    try:
        log("连接 KOOK 客户端...")
        cdp.connect()
        page_url = cdp.evaluate("location.href") or "?"
        log("已附加频道页面: %s" % page_url)

        # 注入监听；页面可能未就绪，最多重试约 45 秒
        log("注入消息监听...")
        status = None
        for attempt in range(30):
            status = cdp.inject()
            if status in ("hooked", "already"):
                break
            time.sleep(1.5)
        log("注入状态: %s" % status)
        if status not in ("hooked", "already"):
            log("错误：未找到语音频道消息容器，请确认已进入语音频道后重试")
            return

        log("自动回复已启动（轮询间隔 %dms）。按 Ctrl+C 停止。"
            % int(cdp.poll_interval * 1000))
        while True:
            msgs = cdp.drain_queue()
            for msg in msgs:
                try:
                    rule = matcher.match(msg)
                    if rule is None:
                        continue
                    reply = matcher.pick_reply(rule)
                    if not reply:
                        continue
                    ok = cdp.send_text(reply)
                    matcher.record_reply(msg, rule)
                    log("[回复] %s(%s) 触发「%s」关键词 -> 已回复: %s 发送=%s"
                        % (msg.get("name"), msg.get("uid"),
                           rule.get("name"), reply, "成功" if ok else "失败"))
                except Exception as e:
                    log("[错误] 单条消息处理失败: %s" % e)
            time.sleep(cdp.poll_interval)
    except KeyboardInterrupt:
        log("已停止")
    except Exception as e:
        log("[致命错误] %s" % e)
    finally:
        cdp.close()
        log("自动回复已退出")


if __name__ == "__main__":
    main()
