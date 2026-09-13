# -*- coding: utf-8 -*-
"""
KOOK 自动回复 · 可视化配置程序（单页面版）

布局：左侧关键词规则列表（每条规则可单独限定触发用户），
右侧基本设置，顶部启动/停止，编辑规则用弹窗。
所有规则操作立即保存到 config.yaml。

运行：python config_editor.py
"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
DEFAULT_CONFIG = {
    "enable": True,
    "account": {"token": "", "uid": ""},
    "target": {"server_id": "", "channel_id": ""},
    "whitelist": {"users": []},
    "rules": [],
    "behavior": {
        "per_user_cooldown": 5, "global_rate": 30,
        "quiet_hours": [], "ignore_prefix": ["/"],
        "ignore_bots": True, "ignore_system": True,
    },
    "logging": {"hit_log": True, "log_file": "hits.log"},
    "network": {"reconnect": True, "reconnect_backoff": [1, 60],
                "notify_on_disconnect": True, "verify_channel_on_start": True},
}

# 回复模式：界面显示中文，config.yaml 中仍存英文值（auto_reply.py 使用）
MODE_LABELS = {"fixed": "固定回复", "random": "随机回复", "sequential": "轮流回复"}
MODE_HINTS = {
    "fixed": "始终回复第一条",
    "random": "每次命中随机挑一条",
    "sequential": "多条回复按顺序轮流使用",
}


def mode_to_label(mode):
    """英文模式值 -> 中文显示名（未知值回退固定回复）"""
    return MODE_LABELS.get(mode, "固定回复")


def label_to_mode(label):
    """中文显示名 -> 英文模式值（未知值回退 fixed）"""
    for k, v in MODE_LABELS.items():
        if v == label:
            return k
    return "fixed"


# ---------------------------------------------------------------------------
# 数据层（可单元测试）
# ---------------------------------------------------------------------------

def load_config(path=CONFIG_PATH):
    """加载配置；文件不存在时返回默认结构"""
    if not os.path.exists(path):
        return dict(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 补齐缺失的顶层键，防止旧配置缺字段
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
    if "users" not in cfg.get("whitelist", {}):
        cfg["whitelist"]["users"] = []
    if "rules" not in cfg:
        cfg["rules"] = []
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    """保存配置到 YAML（注意：会丢失原文件中的注释）"""
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return True


def parse_csv(text):
    """把逗号/顿号分隔的文本解析为字符串列表（去空白、去空）"""
    out = []
    for part in text.replace("，", ",").replace("、", ",").split(","):
        p = part.strip()
        if p:
            out.append(p)
    return out


def format_csv(items):
    """把列表格式化为逗号分隔文本"""
    return ", ".join(str(i) for i in items)


# ---------------------------------------------------------------------------
# 规则编辑弹窗
# ---------------------------------------------------------------------------

class RuleDialog(tk.Toplevel):
    """新增/编辑规则弹窗；确定后 result 返回规则 dict（不含 name），取消返回 None"""

    def __init__(self, parent, rule=None):
        super().__init__(parent)
        self.title("编辑规则" if rule else "新增规则")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None

        pad = {"padx": 12, "pady": 3}
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)

        # 关键词
        ttk.Label(frm, text="关键词").grid(row=0, column=0, sticky="nw", **pad)
        self.kw_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.kw_var, width=40).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(frm, text="别人发的消息里包含这些词就会回复。多个词用逗号分隔，\n"
                            "如：在吗, 在不在（消息里出现任一关键词即触发，原样匹配）",
                  foreground="#888", justify="left").grid(row=1, column=1, sticky="w", **pad)

        # 回复内容
        ttk.Label(frm, text="回复内容").grid(row=2, column=0, sticky="nw", **pad)
        self.reply_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.reply_var, width=40).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(frm, text="命中后要发送的消息。可以写多条，用逗号分隔，\n"
                            "如：在的，在呢，找我有事吗（回哪条由「回复模式」决定）",
                  foreground="#888", justify="left").grid(row=3, column=1, sticky="w", **pad)

        # 回复模式
        ttk.Label(frm, text="回复模式").grid(row=4, column=0, sticky="nw", **pad)
        self.mode_var = tk.StringVar(value="固定回复")
        ttk.Combobox(frm, textvariable=self.mode_var, values=list(MODE_LABELS.values()),
                     state="readonly", width=20).grid(row=4, column=1, sticky="w", **pad)
        self.hint_var = tk.StringVar()
        ttk.Label(frm, textvariable=self.hint_var, foreground="#888").grid(
            row=5, column=1, sticky="w", **pad)
        ttk.Label(frm, text="写了多条回复时怎么选：\n"
                            "固定=永远回第一条；随机=每次随机挑一条；轮流=按顺序轮着回",
                  foreground="#888", justify="left").grid(row=6, column=1, sticky="w", **pad)
        self.mode_var.trace_add("write", self._update_hint)
        self._update_hint()

        # 冷却 / 优先级
        ttk.Label(frm, text="冷却（秒）").grid(row=7, column=0, sticky="nw", **pad)
        self.cd_var = tk.StringVar(value="0")
        ttk.Entry(frm, textvariable=self.cd_var, width=12).grid(row=7, column=1, sticky="w", **pad)
        ttk.Label(frm, text="同一条规则两次回复之间至少隔这么久，防止刷屏；0 = 不限",
                  foreground="#888", justify="left").grid(row=8, column=1, sticky="w", **pad)
        ttk.Label(frm, text="优先级（大者优先）").grid(row=9, column=0, sticky="nw", **pad)
        self.pri_var = tk.StringVar(value="0")
        ttk.Entry(frm, textvariable=self.pri_var, width=12).grid(row=9, column=1, sticky="w", **pad)
        ttk.Label(frm, text="一条消息同时命中多条规则时，谁先回复；数字越大越优先，0 = 普通",
                  foreground="#888", justify="left").grid(row=10, column=1, sticky="w", **pad)

        # 仅回复这些人
        ttk.Label(frm, text="仅回复这些人").grid(row=11, column=0, sticky="nw", **pad)
        self.users_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.users_var, width=40).grid(row=11, column=1, sticky="w", **pad)
        ttk.Label(frm, text="可选。这条规则只对填的人生效，多个用逗号分隔，\n"
                            "如：10001, BD-小锦#2059（用户ID或用户名都可以）\n"
                            "留空 = 任何人都能触发",
                  foreground="#888", justify="left").grid(row=12, column=1, sticky="w", **pad)

        # 启用
        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="启用这条规则（勾选后规则才生效）",
                        variable=self.enabled_var).grid(
            row=13, column=0, columnspan=2, sticky="w", **pad)

        # 按钮
        btns = ttk.Frame(frm)
        btns.grid(row=14, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btns, text="确定", style="Accent.TButton", command=self._ok).pack(side="left")

        # 预填数据
        if rule:
            self.kw_var.set(format_csv(rule.get("keywords", [])))
            self.reply_var.set(format_csv(rule.get("replies", [])))
            self.mode_var.set(mode_to_label(rule.get("reply_mode", "fixed")))
            self.cd_var.set(str(rule.get("cooldown", 0)))
            self.pri_var.set(str(rule.get("priority", 0)))
            self.users_var.set(format_csv(rule.get("users", [])))
            self.enabled_var.set(bool(rule.get("enabled", True)))

        self.grab_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        # 居中于父窗口
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry("+%d+%d" % (max(x, 0), max(y, 0)))

    def _update_hint(self, *_):
        mode = label_to_mode(self.mode_var.get())
        self.hint_var.set("当前模式说明：" + MODE_HINTS.get(mode, ""))

    def _ok(self):
        keywords = parse_csv(self.kw_var.get())
        replies = parse_csv(self.reply_var.get())
        users = parse_csv(self.users_var.get())
        try:
            cooldown = int(self.cd_var.get() or 0)
            priority = int(self.pri_var.get() or 0)
        except ValueError:
            messagebox.showwarning("提示", "冷却和优先级必须是数字", parent=self)
            return
        if not keywords:
            messagebox.showwarning("提示", "至少填一个关键词", parent=self)
            return
        if not replies:
            messagebox.showwarning("提示", "至少填一条回复", parent=self)
            return
        if cooldown < 0 or priority < 0:
            messagebox.showwarning("提示", "冷却和优先级不能为负数", parent=self)
            return
        self.result = {
            "enabled": self.enabled_var.get(),
            "keywords": keywords, "replies": replies,
            "reply_mode": label_to_mode(self.mode_var.get()),
            "cooldown": cooldown, "priority": priority,
            "users": users,
        }
        self.destroy()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KOOK 自动回复 · 可视化配置")
        self.geometry("1020x660")
        self.minsize(880, 580)

        # 视觉主题
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.style.configure("Accent.TButton", foreground="#ffffff",
                             background="#2f9e44", font=("", 9, "bold"))
        self.style.map("Accent.TButton",
                       background=[("active", "#2b8a3e"), ("disabled", "#94d3a2")])
        self.style.configure("Treeview", rowheight=24)

        self.cfg = load_config()
        self._bot_proc = None
        self._build_ui()
        self._refresh_rules()
        self._load_behavior()

    # ---------- 界面搭建 ----------
    def _build_ui(self):
        # 顶部：状态 + 启动/停止
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="KOOK 自动回复", font=("", 11, "bold")).pack(side="left")
        self.state_var = tk.StringVar(value="未启动")
        ttk.Label(top, textvariable=self.state_var, foreground="#555").pack(side="left", padx=(10, 0))
        self.btn_stop = ttk.Button(top, text="停止", command=self.stop_bot, state="disabled")
        self.btn_stop.pack(side="right")
        self.btn_start = ttk.Button(top, text="启动", command=self.start_bot,
                                    style="Accent.TButton")
        self.btn_start.pack(side="right", padx=6)
        ttk.Label(top, text="启动=开始监听并自动回复；停止=结束监听",
                  foreground="#888").pack(side="right", padx=10)

        body = ttk.Frame(self, padding=(10, 4))
        body.pack(fill="both", expand=True)

        # ---- 左：关键词规则 ----
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        lh = ttk.Frame(left)
        lh.pack(fill="x")
        ttk.Label(lh, text="关键词规则", font=("", 10, "bold")).pack(side="left")
        ttk.Button(lh, text="+ 新增规则", command=lambda: self._open_editor(None)).pack(side="right")

        self.rule_tree = ttk.Treeview(
            left, columns=("enabled", "keywords", "replies", "users", "edit", "del"),
            show="headings", selectmode="browse")
        heads = [("enabled", "启用"), ("keywords", "关键词"), ("replies", "回复内容"),
                 ("users", "仅限用户"), ("edit", "编辑"), ("del", "删除")]
        widths = [40, 150, 170, 130, 45, 45]
        for (col, text), w in zip(heads, widths):
            self.rule_tree.heading(col, text=text)
            self.rule_tree.column(col, width=w, anchor="center")
        self.rule_tree.pack(fill="both", expand=True, pady=(6, 0))
        self.rule_tree.bind("<Double-1>", self._on_double_click)
        self.rule_tree.bind("<Button-1>", self._on_cell_click)

        lb = ttk.Frame(left)
        lb.pack(fill="x", pady=6)
        ttk.Button(lb, text="删除选中", command=self._delete_selected).pack(side="left", padx=2)
        ttk.Button(lb, text="复制选中", command=self._duplicate_selected).pack(side="left", padx=2)
        ttk.Label(lb, text="双击行或点「编辑」修改规则；增删改自动保存",
                  foreground="#888").pack(side="left", padx=8)

        # ---- 右：基本设置 ----
        right = ttk.LabelFrame(body, text=" 基本设置 ", padding=12)
        right.pack(side="right", fill="y", padx=(10, 0))

        self.beh_vars = {}
        self.flag_vars = {}
        rows = [
            ("同一人回复间隔（秒）", "per_user_cooldown",
             "同一个人两次回复之间至少隔这么久，防止连续刷屏；0 = 不限"),
            ("每分钟最多回复", "global_rate",
             "整个程序每分钟最多回多少条；0 = 不限"),
            ("静默时段", "quiet_hours",
             "这个时间段内不回复。格式 HH:MM-HH:MM，如 23:00-07:00；多段用逗号分隔；留空 = 不静默"),
            ("忽略前缀", "ignore_prefix",
             "消息以这些字符开头就不回复，如 /（避免回命令）；多个用逗号分隔"),
        ]
        for r, (label, key, hint) in enumerate(rows):
            ttk.Label(right, text=label).grid(row=r * 2, column=0, sticky="w", pady=(5, 0))
            var = tk.StringVar()
            ttk.Entry(right, textvariable=var, width=24).grid(
                row=r * 2, column=1, sticky="w", padx=8, pady=(5, 0))
            ttk.Label(right, text=hint, foreground="#888", wraplength=290,
                      justify="left").grid(row=r * 2 + 1, column=0, columnspan=2, sticky="w")
            self.beh_vars[key] = var

        sw = ttk.Frame(right)
        sw.grid(row=len(rows) * 2 + 1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        for key, label in (("ignore_bots", "忽略其他机器人消息（不回复机器人）"),
                           ("ignore_system", "忽略系统消息（如进房/退房提示）"),
                           ("hit_log", "记录命中日志（存到 hits.log，方便排查）")):
            var = tk.BooleanVar()
            ttk.Checkbutton(sw, text=label, variable=var).pack(anchor="w", pady=1)
            self.flag_vars[key] = var

        self.wl_label_var = tk.StringVar()
        self._update_wl_label()
        ttk.Label(right, textvariable=self.wl_label_var, foreground="#888", wraplength=290,
                  justify="left").grid(
            row=len(rows) * 2 + 2, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Button(right, text="清空全局白名单", command=self._clear_global_whitelist).grid(
            row=len(rows) * 2 + 3, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        ttk.Button(right, text="保存设置", command=self._save_behavior).grid(
            row=len(rows) * 2 + 4, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        # 底部状态栏
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, foreground="#555",
                  padding=(10, 4)).pack(fill="x", side="bottom")

    # ---------- 规则列表 ----------
    def _refresh_rules(self):
        self.rule_tree.delete(*self.rule_tree.get_children())
        for i, r in enumerate(self.cfg.get("rules", [])):
            users = r.get("users") or []
            self.rule_tree.insert("", "end", iid=str(i), values=(
                "是" if r.get("enabled", True) else "否",
                format_csv(r.get("keywords", []))[:22],
                format_csv(r.get("replies", []))[:22],
                format_csv(users)[:20] if users else "全部",
                "编辑", "删",
            ))
        self._update_status()

    def _update_status(self, extra=None):
        n_rules = len(self.cfg.get("rules", []))
        base = "规则 %d 条 · 操作自动保存" % n_rules
        self.status_var.set("%s · %s" % (extra, base) if extra else base)

    def _gen_rule_name(self):
        """自动生成内部唯一名称（冷却/轮流计数用，用户不可见）"""
        import time
        self._name_seq = getattr(self, "_name_seq", 0) + 1
        return "rule_%d_%d" % (int(time.time() * 1000), self._name_seq)

    def _on_cell_click(self, event):
        """点击「编辑/删除」列触发对应操作"""
        if self.rule_tree.identify("region", event.x, event.y) != "cell":
            return
        row = self.rule_tree.identify_row(event.y)
        col = self.rule_tree.identify_column(event.x)
        if not row or not col:
            return
        col_idx = int(col.replace("#", "")) - 1
        iid = int(row)
        if 0 <= iid < len(self.cfg.get("rules", [])):
            if col_idx == 4:
                self._open_editor(iid)
            elif col_idx == 5:
                self._delete_rule(iid)

    def _on_double_click(self, _event=None):
        sel = self.rule_tree.selection()
        if sel:
            self._open_editor(int(sel[0]))

    def _open_editor(self, idx):
        """idx 为 None=新增；否则编辑对应规则"""
        rule = self.cfg["rules"][idx] if idx is not None else None
        dlg = RuleDialog(self, rule=rule)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        if idx is None:
            new_rule = dlg.result
            new_rule["name"] = self._gen_rule_name()
            self.cfg["rules"].append(new_rule)
            self.status_var.set("已新增规则（自动保存）")
        else:
            # 编辑：保留原内部名称
            self.cfg["rules"][idx].update(dlg.result)
            self.status_var.set("已更新规则（自动保存）")
        self._refresh_rules()
        self._auto_save()

    def _delete_rule(self, idx):
        keywords = format_csv(self.cfg["rules"][idx].get("keywords", []))[:20]
        if messagebox.askyesno("确认", "删除这条规则（关键词：%s）？" % keywords):
            del self.cfg["rules"][idx]
            self._refresh_rules()
            self.status_var.set("已删除规则（自动保存）")
            self._auto_save()

    def _delete_selected(self):
        sel = self.rule_tree.selection()
        if sel:
            self._delete_rule(int(sel[0]))

    def _duplicate_selected(self):
        sel = self.rule_tree.selection()
        if not sel:
            return
        import copy
        i = int(sel[0])
        rule = copy.deepcopy(self.cfg["rules"][i])
        rule["name"] = self._gen_rule_name()
        self.cfg["rules"].append(rule)
        self._refresh_rules()
        self.status_var.set("已复制规则（自动保存）")
        self._auto_save()

    # ---------- 基本设置 ----------
    def _update_wl_label(self):
        n = len(self.cfg.get("whitelist", {}).get("users", []))
        if n:
            self.wl_label_var.set(
                "全局白名单 %d 人：只有这些用户能触发全部规则。"
                "想按规则细分，请在每条规则的「仅回复这些人」里设置。" % n)
        else:
            self.wl_label_var.set(
                "全局白名单为空：触发范围完全由每条规则的「仅回复这些人」"
                "决定（留空 = 任何人）")

    def _clear_global_whitelist(self):
        if not self.cfg.get("whitelist", {}).get("users"):
            messagebox.showinfo("提示", "全局白名单已经是空的")
            return
        if messagebox.askyesno("确认", "清空全局白名单？之后触发范围完全由每条规则的「仅回复这些人」控制。"):
            self.cfg["whitelist"]["users"] = []
            self._update_wl_label()
            self._auto_save("已清空全局白名单")

    def _load_behavior(self):
        b = self.cfg.get("behavior", {})
        self.beh_vars["per_user_cooldown"].set(str(b.get("per_user_cooldown", 0)))
        self.beh_vars["global_rate"].set(str(b.get("global_rate", 0)))
        self.beh_vars["quiet_hours"].set(format_csv(b.get("quiet_hours", [])))
        self.beh_vars["ignore_prefix"].set(format_csv(b.get("ignore_prefix", [])))
        self.flag_vars["ignore_bots"].set(bool(b.get("ignore_bots", True)))
        self.flag_vars["ignore_system"].set(bool(b.get("ignore_system", True)))
        self.flag_vars["hit_log"].set(bool(self.cfg.get("logging", {}).get("hit_log", True)))

    def _collect_behavior(self):
        try:
            b = self.cfg.setdefault("behavior", {})
            b["per_user_cooldown"] = int(self.beh_vars["per_user_cooldown"].get() or 0)
            b["global_rate"] = int(self.beh_vars["global_rate"].get() or 0)
        except ValueError:
            messagebox.showwarning("提示", "数值格式不正确，请检查")
            return False
        b["quiet_hours"] = parse_csv(self.beh_vars["quiet_hours"].get())
        b["ignore_prefix"] = parse_csv(self.beh_vars["ignore_prefix"].get())
        b["ignore_bots"] = bool(self.flag_vars["ignore_bots"].get())
        b["ignore_system"] = bool(self.flag_vars["ignore_system"].get())
        self.cfg.setdefault("logging", {})["hit_log"] = bool(self.flag_vars["hit_log"].get())
        return True

    def _save_behavior(self):
        if self._collect_behavior():
            self._auto_save("已保存设置")

    def _auto_save(self, note="已自动保存"):
        try:
            save_config(self.cfg)
            self.status_var.set("%s（config.yaml）" % note)
        except Exception as e:
            messagebox.showerror("错误", "保存失败：%s" % e)

    # ---------- 启动 / 停止 ----------
    def start_bot(self):
        py = sys.executable
        script = os.path.join(BASE_DIR, "auto_reply.py")
        if not os.path.exists(script):
            messagebox.showerror("错误", "找不到 auto_reply.py")
            return
        placeholders = {str(u) for u in self.cfg.get("whitelist", {}).get("users", [])
                        if str(u) in ("用户ID1", "用户ID2")}
        if placeholders:
            messagebox.showwarning(
                "提示", "全局白名单仍包含占位符「用户ID1/用户ID2」，会拦截所有人。"
                        "请先点右侧「清空全局白名单」再启动。")
            return
        if self._bot_proc and self._bot_proc.poll() is None:
            messagebox.showinfo("提示", "自动回复已在运行")
            return
        try:
            if sys.platform == "win32":
                self._bot_proc = subprocess.Popen(
                    [py, script], creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=BASE_DIR)
            else:
                self._bot_proc = subprocess.Popen([py, script], cwd=BASE_DIR)
        except Exception as e:
            messagebox.showerror("错误", "启动失败：%s" % e)
            return
        self.state_var.set("运行中")
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status_var.set("自动回复已启动（新窗口），日志见 auto_reply.log")
        self._watch_bot()

    def _watch_bot(self):
        """每 2 秒检查自动回复进程是否存活，退出时恢复界面状态"""
        p = self._bot_proc
        if p is None:
            return
        code = p.poll()
        if code is not None:
            self._bot_proc = None
            self.state_var.set("已退出（代码 %s）" % code)
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            self.status_var.set("自动回复已退出，请打开 auto_reply.log 查看原因")
            return
        self.after(2000, self._watch_bot)

    def stop_bot(self):
        if self._bot_proc and self._bot_proc.poll() is None:
            try:
                self._bot_proc.terminate()
            except Exception:
                pass
            self.status_var.set("已发送停止指令")
        self._bot_proc = None
        self.state_var.set("未启动")
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def _on_close(self):
        if self._bot_proc and self._bot_proc.poll() is None:
            if messagebox.askyesno("确认", "自动回复仍在运行，确定退出配置程序？（不会停止自动回复）"):
                self.destroy()
        else:
            self.destroy()


def main():
    app = App()
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
