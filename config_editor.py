# -*- coding: utf-8 -*-
"""
KOOK 自动回复 · 可视化配置程序（单页面版）

布局参考：左侧关键词规则列表（每条规则可单独限定触发用户），
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
    """新增/编辑规则弹窗；确定后通过 result 返回规则 dict，取消返回 None"""

    def __init__(self, parent, rule=None):
        super().__init__(parent)
        self.title("编辑规则" if rule else "新增规则")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        self._rule = rule

        pad = {"padx": 12, "pady": 4}
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)

        # 名称（内部标识，用于轮流回复与冷却区分）
        ttk.Label(frm, text="名称（内部标识）").grid(row=0, column=0, sticky="w", **pad)
        self.name_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.name_var, width=40).grid(row=0, column=1, sticky="w", **pad)

        # 关键词
        ttk.Label(frm, text="关键词（逗号分隔）").grid(row=1, column=0, sticky="w", **pad)
        self.kw_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.kw_var, width=40).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(frm, text="消息里包含任一关键词即触发（原样字符匹配）",
                  foreground="#888").grid(row=2, column=1, sticky="w", **pad)

        # 回复内容
        ttk.Label(frm, text="回复内容（逗号分隔）").grid(row=3, column=0, sticky="w", **pad)
        self.reply_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.reply_var, width=40).grid(row=3, column=1, sticky="w", **pad)

        # 回复模式
        ttk.Label(frm, text="回复模式").grid(row=4, column=0, sticky="w", **pad)
        self.mode_var = tk.StringVar(value="固定回复")
        ttk.Combobox(frm, textvariable=self.mode_var, values=list(MODE_LABELS.values()),
                     state="readonly", width=20).grid(row=4, column=1, sticky="w", **pad)
        self.hint_var = tk.StringVar()
        ttk.Label(frm, textvariable=self.hint_var, foreground="#888").grid(
            row=5, column=1, sticky="w", **pad)
        self.mode_var.trace_add("write", self._update_hint)
        self._update_hint()

        # 冷却 / 优先级
        ttk.Label(frm, text="冷却（秒）").grid(row=6, column=0, sticky="w", **pad)
        self.cd_var = tk.StringVar(value="0")
        ttk.Entry(frm, textvariable=self.cd_var, width=12).grid(row=6, column=1, sticky="w", **pad)
        ttk.Label(frm, text="优先级（大者优先）").grid(row=7, column=0, sticky="w", **pad)
        self.pri_var = tk.StringVar(value="0")
        ttk.Entry(frm, textvariable=self.pri_var, width=12).grid(row=7, column=1, sticky="w", **pad)

        # 仅回复这些人
        ttk.Label(frm, text="仅回复这些人").grid(row=8, column=0, sticky="nw", **pad)
        self.users_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.users_var, width=40).grid(row=8, column=1, sticky="w", **pad)
        ttk.Label(frm, text="可选。填用户ID或用户名，逗号分隔，如：10001, BD-小锦#2059\n留空 = 任何人都能触发",
                  foreground="#888", justify="left").grid(row=9, column=1, sticky="w", **pad)

        # 启用
        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="启用这条规则", variable=self.enabled_var).grid(
            row=10, column=0, columnspan=2, sticky="w", **pad)

        # 按钮
        btns = ttk.Frame(frm)
        btns.grid(row=11, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btns, text="确定", style="Accent.TButton", command=self._ok).pack(side="left")

        # 预填数据
        if rule:
            self.name_var.set(rule.get("name", ""))
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
        self.hint_var.set("模式说明：" + MODE_HINTS.get(mode, ""))

    def _ok(self):
        name = self.name_var.get().strip()
        keywords = parse_csv(self.kw_var.get())
        replies = parse_csv(self.reply_var.get())
        users = parse_csv(self.users_var.get())
        try:
            cooldown = int(self.cd_var.get() or 0)
            priority = int(self.pri_var.get() or 0)
        except ValueError:
            messagebox.showwarning("提示", "冷却和优先级必须是数字", parent=self)
            return
        if not name:
            messagebox.showwarning("提示", "请填写规则名称", parent=self)
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
            "name": name, "enabled": self.enabled_var.get(),
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
        self.geometry("1000x640")
        self.minsize(860, 560)

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
        ttk.Label(lb, text="双击行或点「编辑」打开编辑窗口；增删改自动保存",
                  foreground="#888").pack(side="left", padx=8)

        # ---- 右：基本设置 ----
        right = ttk.LabelFrame(body, text=" 基本设置 ", padding=12)
        right.pack(side="right", fill="y", padx=(10, 0))

        self.beh_vars = {}
        self.flag_vars = {}
        rows = [
            ("同一人回复间隔（秒）", "per_user_cooldown", "0 = 不限"),
            ("每分钟最多回复", "global_rate", "0 = 不限"),
            ("静默时段（如 23:00-07:00）", "quiet_hours", "逗号分隔多段"),
            ("忽略前缀（如 /）", "ignore_prefix", "逗号分隔"),
        ]
        for r, (label, key, hint) in enumerate(rows):
            ttk.Label(right, text=label).grid(row=r, column=0, sticky="w", pady=5)
            var = tk.StringVar()
            ttk.Entry(right, textvariable=var, width=24).grid(row=r, column=1, sticky="w", padx=8, pady=5)
            self.beh_vars[key] = var
        ttk.Label(right, text="提示：" + "；".join(h for _, _, h in rows),
                  foreground="#888", wraplength=280, justify="left").grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=(2, 6))

        sw = ttk.Frame(right)
        sw.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w")
        for key, label in (("ignore_bots", "忽略其他机器人消息"),
                           ("ignore_system", "忽略系统消息"),
                           ("hit_log", "记录命中日志")):
            var = tk.BooleanVar()
            ttk.Checkbutton(sw, text=label, variable=var).pack(anchor="w", pady=1)
            self.flag_vars[key] = var

        n_wl = len(self.cfg.get("whitelist", {}).get("users", []))
        ttk.Label(right, text=("旧版全局白名单仍生效（%d 人，仅这些用户可触发全部规则）"
                               "；如需细分，请在每条规则的「仅回复这些人」里单独设置。" % n_wl),
                  foreground="#888", wraplength=280, justify="left").grid(
            row=len(rows) + 2, column=0, columnspan=2, sticky="w", pady=(8, 4))
        ttk.Button(right, text="保存设置", command=self._save_behavior).grid(
            row=len(rows) + 3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

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
            self.cfg["rules"].append(dlg.result)
            self.status_var.set("已新增规则：%s" % dlg.result["name"])
        else:
            self.cfg["rules"][idx] = dlg.result
            self.status_var.set("已更新规则：%s" % dlg.result["name"])
        self._refresh_rules()
        self._auto_save()

    def _delete_rule(self, idx):
        name = self.cfg["rules"][idx].get("name", "")
        if messagebox.askyesno("确认", "删除规则「%s」？" % name):
            del self.cfg["rules"][idx]
            self._refresh_rules()
            self.status_var.set("已删除规则：%s" % name)
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
        rule["name"] = rule.get("name", "") + "-副本"
        self.cfg["rules"].append(rule)
        self._refresh_rules()
        self.status_var.set("已复制规则：%s（自动保存）" % rule["name"])
        self._auto_save()

    # ---------- 基本设置 ----------
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
        self.status_var.set("自动回复已启动（新窗口），日志见 hits.log")

    def stop_bot(self):
        if self._bot_proc and self._bot_proc.poll() is None:
            try:
                self._bot_proc.terminate()
            except Exception:
                pass
            self.status_var.set("已发送停止指令")
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
