"""Tier 1：真机证据**到位之后**的判据（证据由 `collect_device_evidence.py` 采集）。

这一档刻意与采集分离：

- 采集需要人工扫码（`collect_device_evidence.py`）；
- 判据只读采集产物，把「日志里有」变成「断言里有」。

**没有采集产物时整个模块 `skipTest`，并写明原因**——跳过的理由会出现在测试输出里，
报告的三档表里 Tier 1 记 `UNVERIFIED`。跳过**不是**通过。

Tier 1 按作业（浏览器）拆成两条**并列**的档，各自独立成立：

- **Tier 1a = 真机系统浏览器**：手机用系统浏览器打开 → 报到 → 作答 → 交卷。
- **Tier 1b = 微信内置浏览器（DQ7）**：请求 UA 里必须有 `MicroMessenger/`。

多轮采集怎么共存：`collect_device_evidence.py` 除写「最新一轮」别名 `device-session.json`
外，还按 nonce 写 `device-session-<nonce>.json`（早先被覆盖的轮次可用 `--rebuild-session`
从 `device-<nonce>-requests.jsonl` + `device-<nonce>.db` 重建）。本模块把**所有**轮次都读进来，
按 nonce 去重、按 UA 分派给 1a / 1b —— 所以「跑 1b 会不会把 1a 的账冲掉」不再是个问题。

一条方法边界（`TestTier1aRealDeviceFlow.test_05` 钉住）：**记录客户端 IP/UA 的是本项目的采集代理，
不是产品**。产品 uvicorn 只看得见 `127.0.0.1`（代理在中间转发），所以 `uvicorn_phone_lines` 必然为空；
「非回环客户端」这条证据的来源是代理日志。产品侧能自证的只有 DB 里那条 attempt 行。
"""
from __future__ import annotations

import datetime as dt
import glob
import ipaddress
import json
import os
import unittest

from tests.e2e_m4._harness import ARTIFACTS, dump_evidence, record_evidence

SESSION_GLOB = os.path.join(ARTIFACTS, "device-session*.json")
NEIGHBORS = os.path.join(ARTIFACTS, "lan-neighbors.txt")

# 采集器/驱动里干跑用的固定学生，真机上不该出现（出现了说明采的是假数据）
DRY_RUN_STUDENT = {"name": "验收学生", "student_no": "20260406001"}
BROWSER_UA_MARK = "Mozilla"
WECHAT_UA_MARK = "MicroMessenger"


def _parse(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def load_sessions() -> list[dict]:
    """读入所有采集轮次（按 nonce 去重，信息更全的那份胜出）。"""
    sessions: dict[str, dict] = {}
    for path in sorted(glob.glob(SESSION_GLOB)):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        nonce = str(data.get("nonce") or os.path.basename(path))
        current = sessions.get(nonce)
        if current is None or (len(data.get("phone_requests") or [])
                               > len(current.get("phone_requests") or [])):
            sessions[nonce] = data
    return sorted(sessions.values(),
                  key=lambda item: str((item.get("window") or {}).get("started") or ""))


class _SessionCase(unittest.TestCase):
    """所有 Tier 1 用例的共同前置：没有采集产物就带理由跳过。"""

    sessions: list[dict] = []
    phone_sessions: list[dict] = []
    target: dict | None = None

    @classmethod
    def setUpClass(cls):
        cls.sessions = load_sessions()
        cls.phone_sessions = [item for item in cls.sessions if item.get("phone_requests")]
        if not cls.sessions:
            record_evidence("tier1_status", status="not_collected", pattern=SESSION_GLOB)
            raise unittest.SkipTest(
                "尚未采集真机证据：请先运行 "
                "`python backend/tests/e2e_m4/collect_device_evidence.py` 并让真机走完流程"
                f"（期望产物：{SESSION_GLOB}）")
        record_evidence("tier1_status", status="collected",
                        nonces=[item["nonce"] for item in cls.sessions],
                        phone_nonces=[item["nonce"] for item in cls.phone_sessions])

    def setUp(self):
        if self.target is None:                                   # pragma: no cover
            self.skipTest("本档没有匹配的采集轮次")

    @property
    def phone(self) -> list[dict]:
        return list((self.target or {}).get("phone_requests") or [])


class TestTier1aRealDeviceFlow(_SessionCase):
    """Tier 1a：真机（**系统浏览器**）真的连上了，并且真的走完了全流程。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.target = next((item for item in cls.phone_sessions
                           if not any(WECHAT_UA_MARK in entry["ua"]
                                      for entry in item["phone_requests"])), None)
        if cls.target is None:
            cls.target = None
            raise unittest.SkipTest(
                "没有任何「系统浏览器」真机轮次（phone_requests 非空且 UA 不含 MicroMessenger）")

    def test_01_client_ip_comes_from_a_second_device(self):
        phone = self.phone
        self.assertTrue(phone, f"没有任何非回环客户端请求：{self.target['verdict']}")
        for entry in phone:
            self.assertNotIn(entry["client_ip"], ("127.0.0.1", "::1"),
                             "回环请求混进了 phone_requests")
        first = phone[0]
        ip = ipaddress.ip_address(first["client_ip"])
        self.assertTrue(ip.is_private or ip.is_link_local,
                        f"客户端地址不像局域网地址：{first['client_ip']}")
        record_evidence("tier1a_client", nonce=self.target["nonce"], first=first,
                        count=len(phone),
                        client_ips=sorted({e["client_ip"] for e in phone}))

    def test_02_the_client_address_is_a_real_neighbour_on_that_link(self):
        """把「手机上真的连着这个热点」与「请求真的来自那个地址」对上。

        依据是本机二层邻居表（`lan-neighbors.txt`，采集时用 `arp -a` 抓的）。
        邻居表是**现场一次性事实**，文件不在就跳过这一条，不假装通过。
        """
        if not os.path.exists(NEIGHBORS):
            self.skipTest(f"没有邻居表快照 {NEIGHBORS}（现场事实文件），本条无法判定")
        with open(NEIGHBORS, "r", encoding="utf-8") as handle:
            table = handle.read()
        client_ips = sorted({e["client_ip"] for e in self.phone})
        missing = [ip for ip in client_ips if ip not in table]
        self.assertFalse(missing, f"这些客户端地址不在邻居表里：{missing}")
        record_evidence("tier1a_neighbours", client_ips=client_ips,
                        snapshot=NEIGHBORS, table_lines=len(table.splitlines()))

    def test_03_user_agent_is_recorded_and_looks_like_a_browser(self):
        agents = self.target["phone_user_agents"]
        self.assertTrue(agents, "服务端没有记到任何 UA")
        self.assertTrue(all(BROWSER_UA_MARK in agent for agent in agents), agents)
        # 早先那次采集里，我自己那条 `curl/8.21.0` 存活校验混进过 phone 候选；
        # 这里钉死：非回环请求里不许有命令行客户端，否则「手机请求」是假的。
        for entry in self.phone:
            self.assertNotIn("curl/", entry["ua"].lower(),
                             f"命令行客户端混进了手机请求：{entry}")
        record_evidence("tier1a_agents", nonce=self.target["nonce"], agents=agents)

    def test_04_attempt_row_is_the_product_side_proof(self):
        """产品侧唯一能自证的证据：DB 里那条指向本次 nonce 卷的 attempt 行。"""
        paper = self.target["paper"]
        rows = self.target["attempt_rows"]
        self.assertTrue(rows, "没有落到 attempt 行 —— 交卷没有真正写库")
        self.assertTrue(paper["title"].endswith(self.target["nonce"]),
                        f"卷标题没带本次 nonce：{paper['title']}")
        started = _parse((self.target["window"] or {}).get("started") or "")
        finished = _parse((self.target["window"] or {}).get("finished") or "")
        for row in rows:
            self.assertEqual(row["paper_id"], paper["id"])
            self.assertTrue(row["attempt_uuid"], "attempt_uuid 为空（D76 幂等键）")
            self.assertIsNotNone(row["student"], "attempt 行没有关联学生")
            self.assertNotEqual(row["student"].get("name"), DRY_RUN_STUDENT["name"],
                                "落到库里的学生是干跑学生，不是真机上填的人")
            self.assertNotEqual(row["student"].get("student_no"), DRY_RUN_STUDENT["student_no"],
                                "落到库里的学号是干跑学号，不是真机上填的")
            self.assertTrue(str(row["student"].get("name") or "").strip(), "学生姓名为空")
            self.assertTrue(str(row["student"].get("student_no") or "").strip(), "学号为空")
            when = _parse(row["submitted_at"])
            self.assertIsNotNone(when, row["submitted_at"])
            if started and finished:
                self.assertLessEqual(started, when)
                self.assertLessEqual(when, finished)
        record_evidence("tier1a_attempt", nonce=self.target["nonce"], rows=rows,
                        window=self.target["window"], paper=paper)

    def test_05_client_ip_evidence_comes_from_our_proxy_not_from_the_product(self):
        """把方法边界钉进判据里，防止以后有人把这条当成「产品记了客户端」。"""
        self.assertTrue(self.target["artifacts"]["requests_jsonl"])
        self.assertTrue(self.target["artifacts"]["uvicorn_log"])
        self.assertIn("proxy 0.0.0.0", self.target["server_bound"])
        record_evidence(
            "tier1a_method_boundary",
            recorded_by="backend/tests/e2e_m4/collect_device_evidence.py 的转发代理",
            product_sees_client="127.0.0.1（代理在上游）",
            uvicorn_phone_lines=self.target.get("uvicorn_phone_lines", []),
            note="产品自己的 uvicorn 访问日志里没有手机 IP；"
                 "非回环证据来自本项目采集器，产品侧证据只有 DB 的 attempt 行。",
        )

    def test_06_environment_facts_are_recorded(self):
        self.assertTrue(self.target["lan_ip"], "没记下局域网地址")
        self.assertTrue(self.target["lan_ip_reason"], "没记下「为什么选这张网卡」")
        if not self.target.get("reconstructed"):
            self.assertTrue(self.target["candidates"], "没记下候选网卡清单")
            self.assertIn("covered", self.target.get("firewall") or {})
            self.assertTrue(self.target["proxy_interpreter"], "没记下监听用的是哪个解释器")
        record_evidence("tier1a_environment", nonce=self.target["nonce"],
                        lan_ip=self.target["lan_ip"],
                        reason=self.target["lan_ip_reason"],
                        proxy_interpreter=self.target.get("proxy_interpreter", ""),
                        firewall=self.target.get("firewall") or {},
                        reconstructed=bool(self.target.get("reconstructed")))


class TestTier1bWeChatInnerBrowser(_SessionCase):
    """Tier 1b：微信内置浏览器（DQ7）——**没有 MicroMessenger 请求就是未验证**。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.target = next((item for item in cls.phone_sessions
                           if any(WECHAT_UA_MARK in entry["ua"]
                                  for entry in item["phone_requests"])), None)
        if cls.target is None:
            raise unittest.SkipTest(
                "本次采集（所有轮次）都没有 MicroMessenger 请求 → **DQ7 真机仍未验证**"
                f"；已采轮次：{[item['nonce'] for item in cls.phone_sessions]}")

    def test_01_wechat_ua_is_recorded_with_a_version(self):
        wechat = [entry for entry in self.phone if WECHAT_UA_MARK in entry["ua"]]
        self.assertTrue(wechat, "没有 MicroMessenger 请求")
        for entry in wechat:
            self.assertRegex(entry["ua"], r"MicroMessenger/\d+\.\d+")
            self.assertTrue(entry["path"], entry)
        record_evidence("tier1b_wechat", nonce=self.target["nonce"],
                        wechat_requests=len(wechat),
                        agents=self.target["phone_user_agents"],
                        verdict="verified", entries=wechat)

    def test_02_wechat_run_level_is_opened_only_or_full_flow(self):
        """微信里跑分两级记：**只打开页面** 与 **走完交卷** 不是一回事。

        - 有微信请求但没走到 `POST /api/attempts` → 明确记「能打开」，
          **不记「全流程可用」**（Lead 口径：不许用「能打开」冒充「能用」）；
        - 走到了交卷 → 必须有属于本次 nonce 卷、且关联了学生的 attempt 行。
        """
        paths = " ".join(entry["path"] for entry in self.phone)
        if "/api/attempts" not in paths:
            record_evidence("tier1b_wechat_level", nonce=self.target["nonce"],
                            level="opened_only",
                            paths=sorted({entry["path"] for entry in self.phone}))
            self.skipTest(
                "微信内置浏览器**能打开**学生端，但没有走到交卷（无 POST /api/attempts）"
                "→ 只记「能打开」，不记「全流程可用」")
        rows = self.target["attempt_rows"]
        self.assertTrue(rows, "微信里发出了交卷请求，但 DB 里没有 attempt 行")
        paper = self.target["paper"]
        for row in rows:
            self.assertEqual(row["paper_id"], paper["id"])
            self.assertTrue(row["attempt_uuid"], "attempt_uuid 为空（D76 幂等键）")
            self.assertIsNotNone(row["student"], "attempt 行没有关联学生")
            self.assertNotEqual(row["student"].get("name"), DRY_RUN_STUDENT["name"])
        record_evidence("tier1b_wechat_level", nonce=self.target["nonce"],
                        level="full_flow", rows=rows, paper=paper)


def tearDownModule():
    print(f"\n[Tier1] 证据已落盘：{dump_evidence()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
