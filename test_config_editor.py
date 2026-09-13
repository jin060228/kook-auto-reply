# -*- coding: utf-8 -*-
"""config_editor 数据层测试：配置读写往返、CSV 解析"""

import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_editor import (load_config, save_config, parse_csv, format_csv,
                           mode_to_label, label_to_mode, MODE_LABELS,
                           DEFAULT_CONFIG)


class TestReplyMode(unittest.TestCase):
    def test_英文值转中文标签(self):
        self.assertEqual(mode_to_label("fixed"), "固定回复")
        self.assertEqual(mode_to_label("random"), "随机回复")
        self.assertEqual(mode_to_label("sequential"), "轮流回复")

    def test_中文标签转英文值(self):
        self.assertEqual(label_to_mode("固定回复"), "fixed")
        self.assertEqual(label_to_mode("随机回复"), "random")
        self.assertEqual(label_to_mode("轮流回复"), "sequential")

    def test_往返一致(self):
        for en, zh in MODE_LABELS.items():
            self.assertEqual(label_to_mode(mode_to_label(en)), en)

    def test_未知值回退(self):
        self.assertEqual(mode_to_label("unknown"), "固定回复")
        self.assertEqual(label_to_mode("不存在的模式"), "fixed")


class TestCsv(unittest.TestCase):
    def test_逗号顿号中文逗号都解析(self):
        self.assertEqual(parse_csv("在吗, 有人吗、在不在，hi"), ["在吗", "有人吗", "在不在", "hi"])

    def test_空项被忽略(self):
        self.assertEqual(parse_csv("a,,b, "), ["a", "b"])

    def test_纯空返回空列表(self):
        self.assertEqual(parse_csv("  "), [])

    def test_格式化往返(self):
        items = ["在吗", "666", "yyds"]
        self.assertEqual(parse_csv(format_csv(items)), items)


class TestConfigIO(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "config.yaml")

    def test_保存加载往返(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["rules"] = [
            {"name": "问候", "enabled": True, "keywords": ["在吗"],
             "replies": ["在的"], "reply_mode": "random",
             "cooldown": 5, "priority": 2},
        ]
        cfg["whitelist"]["users"] = ["10001", "210900388"]
        cfg["behavior"]["quiet_hours"] = ["23:00-07:00"]
        save_config(cfg, self.path)
        loaded = load_config(self.path)
        self.assertEqual(loaded["rules"], cfg["rules"])
        self.assertEqual(loaded["whitelist"]["users"], ["10001", "210900388"])
        self.assertEqual(loaded["behavior"]["quiet_hours"], ["23:00-07:00"])
        self.assertEqual(loaded["target"]["server_id"], "")

    def test_中文内容往返(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["rules"] = [{"name": "测试规则", "enabled": True,
                         "keywords": ["报名", "666"], "replies": ["收到，已登记"],
                         "reply_mode": "fixed", "cooldown": 30, "priority": 1}]
        save_config(cfg, self.path)
        loaded = load_config(self.path)
        self.assertEqual(loaded["rules"][0]["keywords"], ["报名", "666"])
        self.assertEqual(loaded["rules"][0]["replies"], ["收到，已登记"])

    def test_缺失文件返回默认结构(self):
        missing = os.path.join(self.tmpdir, "none.yaml")
        cfg = load_config(missing)
        self.assertIn("rules", cfg)
        self.assertIn("whitelist", cfg)
        self.assertIn("behavior", cfg)

    def test_旧配置缺字段自动补齐(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("enable: true\nrules: []\n")
        cfg = load_config(self.path)
        self.assertIn("account", cfg)
        self.assertIn("whitelist", cfg)
        self.assertEqual(cfg["whitelist"]["users"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
