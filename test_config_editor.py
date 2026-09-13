# -*- coding: utf-8 -*-
"""config_editor 数据层测试：配置读写往返、CSV 解析"""

import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_editor import (load_config, save_config, parse_csv, format_csv,
                           DEFAULT_CONFIG)


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
