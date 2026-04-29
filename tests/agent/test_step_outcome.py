"""tests/agent/test_step_outcome.py — StepOutcome 控制原语测试。"""

from agent.step_outcome import StepOutcome


class TestStepOutcome:
    def test_done_factory(self):
        outcome = StepOutcome.done("result data")
        assert outcome.should_exit is True
        assert outcome.exit_reason == "completed"
        assert outcome.data == "result data"

    def test_continue_factory(self):
        outcome = StepOutcome.continue_("next step")
        assert outcome.next_prompt == "next step"
        assert outcome.should_exit is False

    def test_steer_factory(self):
        outcome = StepOutcome.steer("injected message")
        assert outcome.steer_message == "injected message"
        assert outcome.should_exit is False

    def test_error_factory(self):
        outcome = StepOutcome.error("something broke")
        assert outcome.error == "something broke"
        assert outcome.should_exit is False

    def test_default_should_not_exit(self):
        outcome = StepOutcome()
        assert outcome.should_exit is False
        assert outcome.exit_reason == ""
