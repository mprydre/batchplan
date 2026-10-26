"""batchplan —— 自检测试。

按并发、限流与重试约束排出批处理排程，并预估耗时与成本。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import batchplan  # noqa: E402


class TestBatchPlan(unittest.TestCase):
    def test_concurrency_is_bounded_by_rate(self):
        limits = dict(batchplan.DEFAULT_LIMITS)
        limits.update({"rps": 5, "avg_latency": 2.0, "max_concurrency": 100})
        conc = batchplan.compute_concurrency(limits)
        self.assertLessEqual(conc["effective"], 100)
        self.assertEqual(conc["binding"], "速率上限")

    def test_concurrency_respects_client_cap(self):
        limits = dict(batchplan.DEFAULT_LIMITS)
        limits.update({"rps": 1000, "avg_latency": 1.0, "max_concurrency": 4})
        conc = batchplan.compute_concurrency(limits)
        self.assertEqual(conc["effective"], 4)
        self.assertEqual(conc["binding"], "客户端上限")

    def test_total_duration_grows_with_tasks(self):
        limits = dict(batchplan.DEFAULT_LIMITS)
        small = batchplan.compute_plan(100, limits)
        big = batchplan.compute_plan(1000, limits)
        self.assertGreater(big["timing"]["total_duration"],
                           small["timing"]["total_duration"])

    def test_cost_includes_retry_overhead(self):
        limits = dict(batchplan.DEFAULT_LIMITS)
        limits.update({"failure_rate": 0.2})
        plan = batchplan.compute_plan(1000, limits)
        self.assertGreater(plan["cost"]["total"], plan["cost"]["without_retries"])

    def test_batches_cover_all_tasks(self):
        limits = dict(batchplan.DEFAULT_LIMITS)
        plan = batchplan.compute_plan(1000, limits)
        self.assertEqual(sum(b["tasks"] for b in plan["batches"]), 1000)

    def test_does_not_make_requests(self):
        """防呆：只排程，不发起任何调用。"""
        plan = batchplan.compute_plan(100, batchplan.DEFAULT_LIMITS)
        self.assertNotIn("requests", plan)
        self.assertIn("batches", plan)
