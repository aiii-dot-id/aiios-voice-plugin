"""Phase accounting cannot erase a transient peak or include shutdown as speech."""
import pytest

from scripts.audit_native_asr_refresh import phases


def samples():
    return [{'observed_monotonic_ns': i * 100_000_000,
             'processes': [{'rss': 100 if i < 10 else 40}, {'rss': 1}]} for i in range(140)]


def test_startup_and_active_session_are_separate_measurements():
    r = phases(samples(), 1_000_000_000, 13_000_000_000)
    assert r['startup_peak_sampled_rss_bytes'] == 101
    assert r['active_max_sampled_rss_bytes'] == 41
    assert r['active_samples'] == 121 and r['active_seconds'] == 12


@pytest.mark.parametrize('damage', ['clock', 'gap', 'missing-worker', 'short'])
def test_untrustworthy_phase_accounting_is_refused(damage):
    rows = samples(); complete = 13_000_000_000
    if damage == 'clock': rows[20]['observed_monotonic_ns'] = rows[19]['observed_monotonic_ns']
    elif damage == 'gap': del rows[20:24]
    elif damage == 'missing-worker': rows[20]['processes'].pop()
    else: complete = 4_000_000_000
    with pytest.raises(AssertionError): phases(rows, 1_000_000_000, complete)
