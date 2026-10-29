import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'runtime/native/session/native_c_api.cpp').read_text()


def body(start, end):
    at = SOURCE.index(start)
    return SOURCE[at:SOURCE.index(end, at)]


CHANNEL_OWNER = body('struct ChannelOwner final', 'aii_voice_result no_enrollment')
WARM = CHANNEL_OWNER[CHANNEL_OWNER.index('aii_voice_readiness warm() override'):]


def test_a_session_process_runs_no_inference_to_say_it_is_ready():
    assert 'warm_parts' not in WARM
    for stream in ('asr', 'end', 'tts', 'own_vad'):
        assert not re.search(r'\b%s\s*->' % stream, WARM), stream
        assert not re.search(r'\*\s*%s\b' % stream, WARM), stream


def test_its_readiness_comes_from_what_is_loaded_and_the_servers_warm():
    assert 'result.models_loaded=speaker()?5:4' in WARM
    assert 'result.probe_ms=server_probe_ms' in WARM
    assert 'server_probe_ms=uint32_t(probe)' in CHANNEL_OWNER
    assert 'if(probe<1||probe>UINT32_MAX)' in CHANNEL_OWNER


def test_the_server_says_its_warms_time_where_the_session_process_reads_it():
    assert ('factory.description=std::string(warmed.accelerator)+"\\n"+std::to_string(warmed.probe_ms)+"\\n"'
            '+owner->execution_info();') in SOURCE


def test_every_other_owner_still_warms_by_inference():
    assert SOURCE.count('warm_parts(') == 2
    owner = body('struct Owner', 'struct ServerFactory')
    assert 'auto result=warm_parts(models.recognizer(),models.vad(),models.endpoint(),models.synthesizer(),speaker());' in owner
