"""tests/agent/test_task_detector.py — TaskDetector 测试。"""

import pytest

from agent.memory.task_detector import TaskDetector, TaskDetection


@pytest.fixture
def detector():
    return TaskDetector()


class TestDoneSignals:
    def test_thanks(self, detector):
        result = detector.detect("谢谢你的帮助", 3, False)
        assert result.is_complete is True
        assert result.reason == "done_signal"

    def test_done(self, detector):
        result = detector.detect("done, looks good", 2, False)
        assert result.is_complete is True

    def test_ok(self, detector):
        result = detector.detect("ok", 1, False)
        assert result.is_complete is True
        assert result.reason == "done_signal"

    def test_perfect(self, detector):
        result = detector.detect("完美！就这样吧", 4, False)
        assert result.is_complete is True


class TestTopicSwitchSignals:
    def test_next_topic(self, detector):
        result = detector.detect("接下来帮我看看另一个问题", 3, False)
        assert result.is_complete is True
        assert result.reason == "topic_switch"

    def test_by_the_way(self, detector):
        """'对了' 在 _CONTINUE_PATTERNS 中，所以被归为未完成。"""
        result = detector.detect("对了，还有一件事", 2, False)
        # "对了" matches continue_signal, which has highest priority
        assert result.is_complete is False
        assert result.reason == "continue_signal"


class TestContinueSignals:
    def test_continue(self, detector):
        result = detector.detect("继续", 2, False)
        assert result.is_complete is False
        assert result.reason == "continue_signal"

    def test_wrong(self, detector):
        result = detector.detect("不对，不是这个意思", 3, False)
        assert result.is_complete is False

    def test_rewrite(self, detector):
        result = detector.detect("重写一下这段代码", 2, True)
        assert result.is_complete is False

    def test_detail(self, detector):
        result = detector.detect("详细点展开说说", 3, False)
        assert result.is_complete is False


class TestPriorityRules:
    def test_continue_overrides_done(self, detector):
        """未完成信号优先于完成信号。"""
        result = detector.detect("不对，继续改一下", 3, False)
        assert result.is_complete is False
        assert result.reason == "continue_signal"

    def test_topic_switch_overrides_done(self, detector):
        """意图切换被视为完成（旧任务结束）。"""
        result = detector.detect("好的，接下来换个话题", 3, False)
        assert result.is_complete is True
        assert result.reason == "topic_switch"


class TestShortReply:
    def test_short_no_tools(self, detector):
        result = detector.detect("好的", 2, False)
        assert result.is_complete is True

    def test_short_with_tools(self, detector):
        result = detector.detect("好的", 2, True)
        assert result.is_complete is True  # "好的" matches done_signal regardless

    def test_short_no_signal(self, detector):
        result = detector.detect("嗯嗯嗯嗯嗯嗯嗯嗯", 2, False)
        assert result.is_complete is True
        assert result.reason == "short_reply_no_tools"

    def test_medium_no_signal(self, detector):
        result = detector.detect("这是一段没有结束信号的普通消息", 3, False)
        assert result.is_complete is False


class TestEdgeCases:
    def test_empty_input(self, detector):
        result = detector.detect("", 1, False)
        assert result.is_complete is False
        assert result.reason == "empty_input"

    def test_whitespace_input(self, detector):
        result = detector.detect("   ", 1, False)
        assert result.is_complete is False
        assert result.reason == "empty_input"

    def test_default_no_match(self, detector):
        result = detector.detect("帮我写一个Python爬虫程序，需要支持分页和反爬虫", 1, True)
        assert result.is_complete is False
        assert result.reason == "default"

    def test_long_message_default(self, detector):
        result = detector.detect("这是一段很长的工作描述，没有任何结束信号" * 5, 2, True)
        assert result.is_complete is False


class TestTaskDetectionDataclass:
    def test_frozen(self):
        d = TaskDetection(is_complete=True, confidence=0.9, reason="test")
        with pytest.raises(AttributeError):
            d.is_complete = False

    def test_values(self):
        d = TaskDetection(is_complete=False, confidence=0.5, reason="default")
        assert d.is_complete is False
        assert d.confidence == 0.5
        assert d.reason == "default"
