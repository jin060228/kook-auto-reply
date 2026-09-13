# -*- coding: utf-8 -*-
"""RuleMatcher 单元测试：不依赖 KOOK / CDP，纯逻辑验证"""

import os
import sys
import unittest
import copy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auto_reply import RuleMatcher

BASE_CONFIG = {
    "account": {"uid": "210900388"},
    "whitelist": {"users": ["10001", "10002"]},
    "rules": [
        {
            "name": "问候", "enabled": True,
            "keywords": ["在吗", "有人吗"],
            "replies": ["在的"],
            "reply_mode": "fixed",
            "cooldown": 0, "priority": 1,
        },
        {
            "name": "报名", "enabled": True,
            "keywords": ["报名"],
            "replies": ["收到"],
            "reply_mode": "fixed",
            "cooldown": 0, "priority": 2,
        },
    ],
    "behavior": {
        "per_user_cooldown": 0, "global_rate": 0,
        "ignore_prefix": ["/"], "ignore_bots": True, "ignore_system": True,
        "quiet_hours": [],
    },
}


def msg(uid="10001", content="在吗", mid="m1", mtype=9):
    return {"uid": uid, "content": content, "id": mid, "type": mtype, "name": "tester"}


class TestWhiteList(unittest.TestCase):
    def test_白名单外不回复(self):
        m = RuleMatcher(BASE_CONFIG)
        self.assertIsNone(m.match(msg(uid="99999", content="在吗", mid="x1")))

    def test_白名单内回复(self):
        m = RuleMatcher(BASE_CONFIG)
        self.assertIsNotNone(m.match(msg(uid="10001", content="在吗", mid="x2")))

    def test_黑名单模式(self):
        cfg = copy.deepcopy(BASE_CONFIG)
        cfg["whitelist"] = {"mode": "blacklist", "users": ["10001"]}
        m = RuleMatcher(cfg)
        self.assertIsNone(m.match(msg(uid="10001", content="在吗", mid="x3")))
        self.assertIsNotNone(m.match(msg(uid="99999", content="在吗", mid="x4")))


class TestKeywordMatch(unittest.TestCase):
    def test_包含即命中(self):
        m = RuleMatcher(BASE_CONFIG)
        self.assertIsNotNone(m.match(msg(content="今晚有人在吗", mid="k1")))

    def test_原样字符_全半角不归一化(self):
        m = RuleMatcher(BASE_CONFIG)
        # 关键词"在吗"，半角"?"消息不含"在吗"，不命中
        self.assertIsNone(m.match(msg(content="有人?", mid="k2")))

    def test_数字包含误命中(self):
        cfg = copy.deepcopy(BASE_CONFIG)
        cfg["rules"] = [{
            "name": "数字", "enabled": True,
            "keywords": ["666"], "replies": ["收到"],
            "reply_mode": "fixed", "cooldown": 0, "priority": 1,
        }]
        m = RuleMatcher(cfg)
        self.assertIsNotNone(m.match(msg(content="6666", mid="k3")))  # 包含即命中

    def test_未命中关键词不回复(self):
        m = RuleMatcher(BASE_CONFIG)
        self.assertIsNone(m.match(msg(content="完全无关", mid="k4")))

    def test_priority取最高(self):
        m = RuleMatcher(BASE_CONFIG)
        hit = m.match(msg(content="在吗，报名", mid="k5"))
        self.assertEqual(hit["name"], "报名")  # priority 2 > 1


class TestSelfAndDedup(unittest.TestCase):
    def test_自己消息不回复(self):
        m = RuleMatcher(BASE_CONFIG)
        self.assertIsNone(m.match(msg(uid="210900388", content="在吗", mid="s1")))

    def test_同消息只回一次(self):
        m = RuleMatcher(BASE_CONFIG)
        hit = m.match(msg(mid="s2"))
        self.assertIsNotNone(hit)
        m.record_reply(msg(mid="s2"), hit)
        self.assertIsNone(m.match(msg(mid="s2")))


class TestCooldownAndRate(unittest.TestCase):
    def test_同规则冷却(self):
        cfg = copy.deepcopy(BASE_CONFIG)
        cfg["rules"][0]["cooldown"] = 100
        m = RuleMatcher(cfg)
        hit = m.match(msg(mid="c1"))
        self.assertIsNotNone(hit)
        m.record_reply(msg(mid="c1"), hit)
        self.assertIsNone(m.match(msg(mid="c2")))  # 冷却期内同规则不回复
        # 不同规则可回复
        hit2 = m.match(msg(content="报名", mid="c3"))
        self.assertEqual(hit2["name"], "报名")

    def test_同一人冷却(self):
        cfg = copy.deepcopy(BASE_CONFIG)
        cfg["behavior"]["per_user_cooldown"] = 100
        m = RuleMatcher(cfg)
        hit = m.match(msg(uid="10001", content="在吗", mid="c4"))
        m.record_reply(msg(uid="10001", mid="c4"), hit)
        self.assertIsNone(m.match(msg(uid="10001", content="有人吗", mid="c5")))

    def test_全局频率上限(self):
        cfg = copy.deepcopy(BASE_CONFIG)
        cfg["behavior"]["global_rate"] = 2
        m = RuleMatcher(cfg)
        for i in range(3):
            hit = m.match(msg(content="在吗", mid=f"g{i}"))
            if hit:
                m.record_reply(msg(mid=f"g{i}"), hit)
        self.assertIsNone(m.match(msg(content="在吗", mid="g9")))  # 第3条后超限


class TestPrefixAndQuiet(unittest.TestCase):
    def test_忽略前缀(self):
        m = RuleMatcher(BASE_CONFIG)
        self.assertIsNone(m.match(msg(content="/help", mid="p1")))

    def test_系统消息忽略(self):
        m = RuleMatcher(BASE_CONFIG)
        self.assertIsNone(m.match(msg(content="在吗", mid="p2", mtype=2)))

    def test_静默时段(self):
        import time as _t
        cfg = copy.deepcopy(BASE_CONFIG)
        now = _t.localtime()
        hm = now.tm_hour * 60 + now.tm_min
        start = hm - 1
        end = hm + 1
        fmt = lambda v: "%02d:%02d" % (v // 60, v % 60)
        cfg["behavior"]["quiet_hours"] = ["%s-%s" % (fmt(start), fmt(end))]
        m = RuleMatcher(cfg)
        self.assertIsNone(m.match(msg(content="在吗", mid="q1")))


class TestReplyMode(unittest.TestCase):
    def test_random_返回列表内(self):
        cfg = copy.deepcopy(BASE_CONFIG)
        cfg["rules"][0]["reply_mode"] = "random"
        cfg["rules"][0]["replies"] = ["A", "B"]
        m = RuleMatcher(cfg)
        hit = m.match(msg(content="在吗", mid="r1"))
        self.assertIn(m.pick_reply(hit), ["A", "B"])

    def test_sequential_轮流(self):
        cfg = copy.deepcopy(BASE_CONFIG)
        cfg["rules"][0]["reply_mode"] = "sequential"
        cfg["rules"][0]["replies"] = ["A", "B"]
        m = RuleMatcher(cfg)
        hit = m.match(msg(content="在吗", mid="r2"))
        self.assertEqual(m.pick_reply(hit), "A")
        self.assertEqual(m.pick_reply(hit), "B")
        self.assertEqual(m.pick_reply(hit), "A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
