# -*- coding: utf-8 -*-
"""
KOOK 自动回复 · 可视化配置程序

功能：
- 图形化管理关键词规则（增删改、启用/停用、回复模式、冷却、优先级）
- 管理触发白名单（用户 ID 列表）
- 调整全局行为（同一人冷却、频率上限、静默时段、忽略前缀）
- 一键保存配置到 config.yaml、一键启动自动回复脚本

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
# 图形界面
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KOOK 自动回复 · 可视化配置")
        self.geometry("1000x660")
        self.minsize(820, 560)

        # 视觉主题：clam 更现代；强调按钮（启动自动回复）用绿色
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
        self._build_ui()
        self._refresh_rules()
        self._refresh_whitelist()
        self._load_behavior()

    # ---------- 界面搭建 ----------
    def _build_ui(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self._build_rules_tab()
        self._build_whitelist_tab()
        self._build_behavior_tab()

        # 底部按钮
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=8)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status_var, foreground="#555").pack(side="left")
        ttk.Button(bar, text="保存配置", command=self.save).pack(side="right", padx=4)
        ttk.Button(bar, text="启动自动回复", command=self.start_bot,
                   style="Accent.TButton").pack(side="right", padx=4)
        ttk.Button(bar, text="退出", command=self.destroy).pack(side="right", padx=4)

    def _build_rules_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=" 关键词规则 ")

        # 左侧列表
        left = ttk.Frame(tab)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=6)
        self.rule_tree = ttk.Treeview(left, columns=("enabled", "keywords", "replies", "mode", "cd", "pri"),
                                      show="headings", selectmode="browse")
        self.rule_tree.heading("enabled", text="启用")
        self.rule_tree.heading("keywords", text="关键词")
        self.rule_tree.heading("replies", text="回复")
        self.rule_tree.heading("mode", text="模式")
        self.rule_tree.heading("cd", text="冷却")
        self.rule_tree.heading("pri", text="优先级")
        for col, w in zip(("enabled", "keywords", "replies", "mode", "cd", "pri"),
                          (44, 180, 180, 90, 50, 60)):
            self.rule_tree.column(col, width=w, anchor="center")
        self.rule_tree.pack(fill="both", expand=True)
        self.rule_tree.bind("<<TreeviewSelect>>", self._on_rule_select)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="添加规则", command=self.add_rule).pack(side="left", padx=2)
        ttk.Button(btns, text="删除选中", command=self.delete_rule).pack(side="left", padx=2)
        ttk.Button(btns, text="复制选中", command=self.duplicate_rule).pack(side="left", padx=2)

        # 右侧编辑表单
        right = ttk.LabelFrame(tab, text=" 编辑规则 ")
        right.pack(side="right", fill="y", padx=(6, 0), pady=6)
        self.form = {}
        rows = [
            ("名称", "name", None),
            ("关键词（逗号分隔）", "keywords", None),
            ("回复（逗号分隔）", "replies", None),
            ("回复模式", "reply_mode", ("random", "fixed", "sequential")),
            ("冷却（秒）", "cooldown", None),
            ("优先级（大者优先）", "priority", None),
        ]
        for r, (label, key, values) in enumerate(rows):
            ttk.Label(right, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=4)
            if values:
                var = tk.StringVar()
                box = ttk.Combobox(right, textvariable=var,
                                   values=list(MODE_LABELS.values()),
                                   state="readonly", width=18)
                box.current(0)
                self.form[key] = box
            else:
                var = tk.StringVar()
                ent = ttk.Entry(right, textvariable=var, width=24)
                self.form[key] = ent
            self.form[key].grid(row=r, column=1, sticky="w", padx=6, pady=4)

        # 回复模式说明（随选择动态更新）
        hint_row = len(rows)
        self.mode_hint_var = tk.StringVar()
        ttk.Label(right, textvariable=self.mode_hint_var, foreground="#888",
                  wraplength=240, justify="left").grid(
            row=hint_row, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))
        self.form["reply_mode"].bind("<<ComboboxSelected>>", self._update_mode_hint)
        self._update_mode_hint()

        enabled_row = hint_row + 1
        self.form["enabled_var"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="启用此规则", variable=self.form["enabled_var"]).grid(
            row=enabled_row, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Button(right, text="应用到选中规则", command=self.apply_rule).grid(
            row=enabled_row + 1, column=0, columnspan=2, sticky="ew", padx=6, pady=8)

    def _build_whitelist_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=" 触发白名单 ")

        ttk.Label(tab, text="只有下列用户 ID 发的消息才会触发自动回复（留空 = 全部忽略）").pack(anchor="w", padx=8, pady=(8, 2))
        ttk.Label(tab, text="提示：KOOK 开启开发者模式后，右键用户头像可复制用户 ID", foreground="#777").pack(anchor="w", padx=8)

        self.whitelist_box = tk.Listbox(tab, height=12)
        self.whitelist_box.pack(fill="both", expand=True, padx=8, pady=6)

        row = ttk.Frame(tab)
        row.pack(fill="x", padx=8)
        self.whitelist_entry = ttk.Entry(row, width=24)
        self.whitelist_entry.pack(side="left")
        ttk.Button(row, text="添加", command=self.add_whitelist).pack(side="left", padx=4)
        ttk.Button(row, text="删除选中", command=self.delete_whitelist).pack(side="left", padx=4)
        ttk.Button(row, text="清空", command=self.clear_whitelist).pack(side="left", padx=4)

    def _build_behavior_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=" 全局设置 ")

        form = ttk.Frame(tab)
        form.pack(anchor="w", padx=12, pady=12)
        self.beh_vars = {}
        rows = [
            ("同一人冷却（秒）", "per_user_cooldown", "0 = 不限"),
            ("每分钟最多回复", "global_rate", "0 = 不限"),
            ("静默时段（如 23:00-07:00）", "quiet_hours", "逗号分隔多段，留空 = 不静默"),
            ("忽略前缀（如 /）", "ignore_prefix", "逗号分隔"),
        ]
        for r, (label, key, hint) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=6)
            var = tk.StringVar()
            ttk.Entry(form, textvariable=var, width=30).grid(row=r, column=1, sticky="w", padx=8, pady=6)
            ttk.Label(form, text=hint, foreground="#888").grid(row=r, column=2, sticky="w")
            self.beh_vars[key] = var

        # 开关
        sw = ttk.Frame(tab)
        sw.pack(anchor="w", padx=12)
        self.flag_vars = {}
        for key, label in (("ignore_bots", "忽略其他机器人消息"),
                           ("ignore_system", "忽略系统消息"),
                           ("hit_log", "记录命中日志")):
            var = tk.BooleanVar()
            ttk.Checkbutton(sw, text=label, variable=var).pack(anchor="w", pady=2)
            self.flag_vars[key] = var

        ttk.Label(tab, text="提示：quiet_hours 格式为 HH:MM-HH:MM，跨天用 23:00-07:00 表示",
                  foreground="#777").pack(anchor="w", padx=12, pady=(6, 0))

    # ---------- 规则页逻辑 ----------
    def _update_mode_hint(self, _event=None):
        mode = label_to_mode(self.form["reply_mode"].get())
        self.mode_hint_var.set("模式说明：" + MODE_HINTS.get(mode, ""))

    def _refresh_rules(self):
        self.rule_tree.delete(*self.rule_tree.get_children())
        for i, r in enumerate(self.cfg.get("rules", [])):
            self.rule_tree.insert("", "end", iid=str(i), values=(
                "是" if r.get("enabled", True) else "否",
                format_csv(r.get("keywords", []))[:30],
                format_csv(r.get("replies", []))[:30],
                mode_to_label(r.get("reply_mode", "fixed")),
                r.get("cooldown", 0),
                r.get("priority", 0),
            ))
        self._update_status()

    def _update_status(self, extra=None):
        n_rules = len(self.cfg.get("rules", []))
        n_wl = len(self.cfg.get("whitelist", {}).get("users", []))
        base = "规则 %d 条 · 白名单 %d 人" % (n_rules, n_wl)
        self.status_var.set("%s · %s" % (extra, base) if extra else base)

    def _on_rule_select(self, _event=None):
        sel = self.rule_tree.selection()
        if not sel:
            return
        i = int(sel[0])
        rules = self.cfg.get("rules", [])
        if 0 <= i < len(rules):
            r = rules[i]
            self.form["name"].delete(0, "end")
            self.form["name"].insert(0, r.get("name", ""))
            self.form["keywords"].delete(0, "end")
            self.form["keywords"].insert(0, format_csv(r.get("keywords", [])))
            self.form["replies"].delete(0, "end")
            self.form["replies"].insert(0, format_csv(r.get("replies", [])))
            mode = r.get("reply_mode", "fixed")
            self.form["reply_mode"].set(mode_to_label(mode))
            self._update_mode_hint()
            self.form["cooldown"].delete(0, "end")
            self.form["cooldown"].insert(0, str(r.get("cooldown", 0)))
            self.form["priority"].delete(0, "end")
            self.form["priority"].insert(0, str(r.get("priority", 0)))
            self.form["enabled_var"].set(bool(r.get("enabled", True)))
            # 状态栏展示该条规则的完整关键词与回复（列表截断之外的补充）
            self._update_status("关键词：%s → 回复：%s" % (
                format_csv(r.get("keywords", [])), format_csv(r.get("replies", []))))

    def _get_form_values(self):
        """从表单读取规则字段，返回 dict 或抛 ValueError"""
        name = self.form["name"].get().strip()
        keywords = parse_csv(self.form["keywords"].get())
        replies = parse_csv(self.form["replies"].get())
        mode = label_to_mode(self.form["reply_mode"].get())
        cooldown = int(self.form["cooldown"].get() or 0)
        priority = int(self.form["priority"].get() or 0)
        if not name:
            raise ValueError("规则名称不能为空")
        if not keywords:
            raise ValueError("至少填一个关键词")
        if not replies:
            raise ValueError("至少填一条回复")
        if cooldown < 0 or priority < 0:
            raise ValueError("冷却和优先级不能为负数")
        return {
            "name": name, "enabled": self.form["enabled_var"].get(),
            "keywords": keywords, "replies": replies,
            "reply_mode": mode, "cooldown": cooldown, "priority": priority,
        }

    def add_rule(self):
        try:
            rule = self._get_form_values()
        except ValueError as e:
            messagebox.showwarning("提示", str(e))
            return
        self.cfg["rules"].append(rule)
        self._refresh_rules()
        self.rule_tree.selection_set(str(len(self.cfg["rules"]) - 1))
        self.status_var.set("已添加规则：%s（记得保存）" % rule["name"])

    def apply_rule(self):
        sel = self.rule_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在左侧选择一条规则")
            return
        try:
            rule = self._get_form_values()
        except ValueError as e:
            messagebox.showwarning("提示", str(e))
            return
        i = int(sel[0])
        self.cfg["rules"][i] = rule
        self._refresh_rules()
        self.rule_tree.selection_set(str(i))
        self.status_var.set("已更新规则：%s（记得保存）" % rule["name"])

    def delete_rule(self):
        sel = self.rule_tree.selection()
        if not sel:
            return
        i = int(sel[0])
        name = self.cfg["rules"][i].get("name", "")
        if messagebox.askyesno("确认", "删除规则「%s」？" % name):
            del self.cfg["rules"][i]
            self._refresh_rules()
            self.status_var.set("已删除规则（记得保存）")

    def duplicate_rule(self):
        sel = self.rule_tree.selection()
        if not sel:
            return
        i = int(sel[0])
        import copy
        rule = copy.deepcopy(self.cfg["rules"][i])
        rule["name"] = rule.get("name", "") + "-副本"
        self.cfg["rules"].append(rule)
        self._refresh_rules()
        self.rule_tree.selection_set(str(len(self.cfg["rules"]) - 1))
        self.status_var.set("已复制规则（记得保存）")

    # ---------- 白名单页逻辑 ----------
    def _refresh_whitelist(self):
        self.whitelist_box.delete(0, "end")
        for uid in self.cfg.get("whitelist", {}).get("users", []):
            self.whitelist_box.insert("end", str(uid))
        self._update_status()

    def add_whitelist(self):
        uid = self.whitelist_entry.get().strip()
        if not uid:
            messagebox.showinfo("提示", "请输入用户 ID")
            return
        users = self.cfg["whitelist"]["users"]
        if uid not in users:
            users.append(uid)
            self._refresh_whitelist()
            self.whitelist_entry.delete(0, "end")
            self.status_var.set("已添加白名单：%s（记得保存）" % uid)
        else:
            messagebox.showinfo("提示", "该用户已在白名单中")

    def delete_whitelist(self):
        sel = self.whitelist_box.curselection()
        if not sel:
            return
        users = self.cfg["whitelist"]["users"]
        del users[sel[0]]
        self._refresh_whitelist()
        self.status_var.set("已删除（记得保存）")

    def clear_whitelist(self):
        if messagebox.askyesno("确认", "清空全部白名单？"):
            self.cfg["whitelist"]["users"] = []
            self._refresh_whitelist()
            self.status_var.set("已清空白名单（记得保存）")

    # ---------- 全局设置页逻辑 ----------
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
        b = self.cfg.setdefault("behavior", {})
        b["per_user_cooldown"] = int(self.beh_vars["per_user_cooldown"].get() or 0)
        b["global_rate"] = int(self.beh_vars["global_rate"].get() or 0)
        b["quiet_hours"] = parse_csv(self.beh_vars["quiet_hours"].get())
        b["ignore_prefix"] = parse_csv(self.beh_vars["ignore_prefix"].get())
        b["ignore_bots"] = bool(self.flag_vars["ignore_bots"].get())
        b["ignore_system"] = bool(self.flag_vars["ignore_system"].get())
        self.cfg.setdefault("logging", {})["hit_log"] = bool(self.flag_vars["hit_log"].get())

    # ---------- 保存 / 启动 ----------
    def save(self):
        try:
            self._collect_behavior()
        except ValueError:
            messagebox.showwarning("提示", "全局设置中的数值格式不正确，请检查")
            return
        try:
            save_config(self.cfg)
        except Exception as e:
            messagebox.showerror("错误", "保存失败：%s" % e)
            return
        self.status_var.set("已保存到 config.yaml")

    def start_bot(self):
        py = sys.executable
        script = os.path.join(BASE_DIR, "auto_reply.py")
        if not os.path.exists(script):
            messagebox.showerror("错误", "找不到 auto_reply.py")
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen([py, script], creationflags=subprocess.CREATE_NEW_CONSOLE,
                                 cwd=BASE_DIR)
            else:
                subprocess.Popen([py, script], cwd=BASE_DIR)
            self.status_var.set("已启动自动回复（新窗口）")
        except Exception as e:
            messagebox.showerror("错误", "启动失败：%s" % e)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
