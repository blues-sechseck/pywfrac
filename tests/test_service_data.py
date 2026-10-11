"""Per-code refusal bookkeeping and the selection it constrains."""

from pywfrac import (
    SERVICE_DATA_BUDGET,
    CodeState,
    ServiceDataRefusals,
)

A, B, C = 0x11, 0x82, 0xB1


def _proven(*codes: int) -> ServiceDataRefusals:
    refusals = ServiceDataRefusals()
    for code in codes:
        refusals.note_answered(code)
    return refusals


def test_a_new_code_is_untested():
    refusals = ServiceDataRefusals()
    assert refusals.state(A) is CodeState.UNTESTED
    assert refusals.has_unproven([A])
    assert not refusals.has_unproven([])


def test_a_refusal_with_only_proven_codes_blames_nobody():
    refusals = _proven(A, B)
    assert refusals.note_refused((A, B)) == ()
    assert refusals.state(A) is refusals.state(B) is CodeState.OK
    assert not refusals.has_unproven((A, B))


def test_one_unproven_code_among_proven_ones_is_the_suspect():
    refusals = _proven(A)
    assert refusals.note_refused((A, B)) == ()
    assert refusals.state(B) is CodeState.SUSPECT
    assert refusals.note_refused((A, B)) == (B,)
    assert refusals.state(B) is CodeState.SUSPENDED
    assert refusals.is_suspended(B)
    assert refusals.state(A) is CodeState.OK
    assert refusals.admit([A, B]) == (A,)


def test_several_unproven_codes_become_suspects_without_counting():
    refusals = ServiceDataRefusals()
    for _ in range(3):
        assert refusals.note_refused((A, B)) == ()
    assert refusals.state(A) is refusals.state(B) is CodeState.SUSPECT


def test_a_suspended_code_stays_suspended_when_refused_again():
    refusals = ServiceDataRefusals()
    refusals.note_refused((A,))
    assert refusals.note_refused((A,)) == (A,)
    assert refusals.note_refused((A,)) == ()
    assert refusals.note_refused((A, B)) == ()
    assert refusals.state(A) is CodeState.SUSPENDED


def test_an_answer_lifts_a_suspension_and_resets_the_count():
    refusals = ServiceDataRefusals()
    refusals.note_refused((A,))
    refusals.note_refused((A,))
    assert refusals.is_suspended(A)

    refusals.note_answered(A)
    assert refusals.state(A) is CodeState.OK

    # Refusals start counting from zero again.
    assert refusals.note_refused((A, B)) == ()
    refusals.note_answered(B)
    refusals._states[A] = CodeState.UNTESTED
    assert refusals.note_refused((A,)) == ()


def test_admit_stays_within_the_budget_and_keeps_the_order():
    refusals = _proven(*range(0x20, 0x30))
    codes = list(range(0x2F, 0x1F, -1))
    chosen = refusals.admit(codes)
    assert len(chosen) == SERVICE_DATA_BUDGET
    assert chosen == tuple(codes[:SERVICE_DATA_BUDGET])
    assert refusals.admit(codes, budget=2) == (codes[0], codes[1])


def test_the_budget_is_ten_segments():
    assert SERVICE_DATA_BUDGET == 10


def test_a_suspended_code_never_rides():
    refusals = ServiceDataRefusals()
    refusals.note_refused((A,))
    refusals.note_refused((A,))
    assert refusals.admit([A]) == ()


def test_an_untested_code_does_not_ride_with_a_suspect():
    refusals = _proven(A)
    refusals._states[B] = CodeState.SUSPECT
    assert refusals.admit([A, B, C]) == (A, B)


def test_a_suspect_does_not_ride_with_an_untested_code():
    refusals = _proven(A)
    refusals._states[B] = CodeState.SUSPECT
    assert refusals.admit([A, C, B]) == (A, C)


def test_one_suspect_rides_at_a_time():
    refusals = _proven(A)
    refusals._states[B] = CodeState.SUSPECT
    refusals._states[C] = CodeState.SUSPECT
    chosen = refusals.admit([A, B, C])
    assert A in chosen
    assert len({B, C} & set(chosen)) == 1
