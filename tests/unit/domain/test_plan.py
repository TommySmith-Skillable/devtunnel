from devtunnel.domain.plan import Plan, Step, StepGroup, StepOutcome, StepStatus


class _NoOpStep(Step):
    def apply(self, ctx) -> StepOutcome:  # pragma: no cover - not exercised here
        return StepOutcome(StepStatus.APPLIED)


def _step(step_id: str) -> _NoOpStep:
    return _NoOpStep(step_id, f"do {step_id}")


def test_walk_flattens_a_flat_group_in_order():
    group = StepGroup("flat", [_step("a"), _step("b"), _step("c")])

    assert [s.id for s in group.walk()] == ["a", "b", "c"]


def test_walk_flattens_nested_groups_depth_first_preserving_order():
    inner = StepGroup("inner", [_step("b1"), _step("b2")])
    outer = StepGroup("outer", [_step("a"), inner, _step("c")])

    assert [s.id for s in outer.walk()] == ["a", "b1", "b2", "c"]


def test_plan_is_the_root_of_the_composite_tree():
    plan = Plan("install", [StepGroup("group", [_step("x")])])

    assert isinstance(plan, StepGroup)
    assert [s.id for s in plan.walk()] == ["x"]


def test_empty_groups_contribute_nothing_to_the_walk():
    plan = Plan("install", [StepGroup("skipped", []), StepGroup("kept", [_step("only")])])

    assert [s.id for s in plan.walk()] == ["only"]
