import hashlib
import json
from pathlib import Path

MEMBER = 'limits'

_TABLE = (
    ('host_read_ms', 5000, 250, 120000),
    ('host_write_ms', 12000, 250, 120000),
    ('storage_wait_ms', 12000, 250, 120000),
    ('drain_idle_ms', 15000, 250, 120000),
    ('reply_settings_ms', 150, 10, 2000),
    ('abort_ms', 5000, 250, 120000),
    ('capture_close_ms', 45000, 250, 120000),
    ('session_open_ms', 60000, 250, 120000),
    ('opening_notice_ms', 1500, 250, 120000),
    ('control_ms', 2000, 250, 120000),
    ('audio_write_ms', 3000, 250, 120000),
    ('ready_ms', 175000, 1000, 3600000),
    ('control_write_ms', 3000, 250, 120000),
    ('retire_ms', 5000, 250, 120000),
    ('capture_tail_ms', 2000, 250, 120000),
    ('worker_exit_ms', 5500, 250, 120000),
    ('worker_reap_ms', 5000, 250, 120000),
    ('lane_flush_ms', 2000, 250, 120000),
    ('model_call_ms', 30000, 250, 120000),
    ('input_tail_ms', 3000, 250, 120000),
    ('output_take_ms', 15000, 250, 120000),
    ('speaker_match_ms', 15000, 250, 120000),
    ('warm_probe_ms', 40000, 250, 120000),
    ('endpoint_decision_ms', 1000, 10, 2000),
    ('endpoint_retire_ms', 30500, 250, 120000),
    ('separation_min_ms', 4000, 250, 120000),
    ('separation_max_ms', 25000, 250, 120000),
)
MEMBERS = tuple(row[0] for row in _TABLE)
DEFAULTS = {row[0]: row[1] for row in _TABLE}
RANGES = {row[0]: (row[2], row[3]) for row in _TABLE}

HOST_INVOKE_ALLOWANCE_MS = 90000
MARGIN_MS = 500
HOST_ANSWER_MARGIN_MS = 2000

NOT_STATED = ('this runtime profile states no time limits (the "limits" member of voice-runtime.json), '
              'so the limits it would ship with are stated nowhere in its package; rebuild its checkpoint '
              'with a script that states them')


def default_limits():
    return dict(DEFAULTS)


def storage_operation_ms(table):
    whole_read = table['storage_wait_ms'] + MARGIN_MS
    whole_publication = 3 * (table['host_write_ms'] + MARGIN_MS) + whole_read
    return 2 * whole_read + whole_publication + table['control_ms']


def playback_report_ms(table):
    return table['audio_write_ms'] + table['control_ms']


def startup_covers_readiness(table, startup_ms):
    if type(startup_ms) is not int:
        raise ValueError('startup_ms must be a whole number of milliseconds')
    if table['ready_ms'] + HOST_ANSWER_MARGIN_MS > startup_ms:
        raise ValueError('runtime limits: the carrier waits %d ms for its worker to report ready (ready_ms), and '
                         'its set declares %d ms to the host for its start (startup_ms); the wait and %d ms for '
                         'the carrier to say that it passed must end inside what the set declares'
                         % (table['ready_ms'], startup_ms, HOST_ANSWER_MARGIN_MS))


def _named(stated):
    if type(stated) is not dict:
        raise ValueError('runtime limits must be an object of whole milliseconds by name')
    unknown = sorted(str(name) for name in stated if name not in DEFAULTS)
    if unknown:
        raise ValueError('runtime limit ' + unknown[0] + ' is not one the carrier knows')
    return stated


def limits_from(stated):
    table = dict(DEFAULTS)
    for name, value in _named(stated).items():
        if type(value) is not int:
            raise ValueError('runtime limit ' + name + ' must be a whole number of milliseconds')
        table[name] = value
    for name in MEMBERS:
        low, high = RANGES[name]
        if not low <= table[name] <= high:
            raise ValueError('runtime limit %s is %d ms; it must be %d to %d' % (name, table[name], low, high))
    if table['host_write_ms'] < table['host_read_ms']:
        raise ValueError("runtime limits: a write must be given at least a read's time")
    if table['storage_wait_ms'] < table['host_write_ms']:
        raise ValueError('runtime limits: the whole wait for storage must cover one write')
    for what, given in (('a control that uses storage', storage_operation_ms(table)),
                        ('a playback report', playback_report_ms(table))):
        if given + HOST_ANSWER_MARGIN_MS > HOST_INVOKE_ALLOWANCE_MS:
            raise ValueError('runtime limits: %s would be given %d ms, and the host waits %d ms for one '
                             'invocation' % (what, given, HOST_INVOKE_ALLOWANCE_MS))
    if table['worker_exit_ms'] < table['retire_ms'] + MARGIN_MS:
        raise ValueError('runtime limits: this carrier would wait %d ms for its worker to exit and the worker has '
                         '%d ms to retire; the wait must be the longer by %d ms at least'
                         % (table['worker_exit_ms'], table['retire_ms'], MARGIN_MS))
    if table['output_take_ms'] < table['audio_write_ms'] + MARGIN_MS:
        raise ValueError('runtime limits: the engine would wait %d ms for its audio to be taken (output_take_ms) '
                         'and one write of that audio to the host is given %d ms (audio_write_ms); the wait must '
                         'be the longer by %d ms at least'
                         % (table['output_take_ms'], table['audio_write_ms'], MARGIN_MS))
    if table['drain_idle_ms'] < table['input_tail_ms'] + MARGIN_MS:
        raise ValueError("runtime limits: a drain may go %d ms with nothing moving (drain_idle_ms) and a "
                         "conversation's last frames are waited for %d ms (input_tail_ms); the drain's must be "
                         "the longer by %d ms at least"
                         % (table['drain_idle_ms'], table['input_tail_ms'], MARGIN_MS))
    if table['ready_ms'] < table['warm_probe_ms'] + MARGIN_MS:
        raise ValueError("runtime limits: a warm inference is given %d ms (warm_probe_ms) and the worker's "
                         "readiness %d ms (ready_ms); readiness must be the longer by %d ms at least"
                         % (table['warm_probe_ms'], table['ready_ms'], MARGIN_MS))
    if table['endpoint_retire_ms'] < table['model_call_ms'] + MARGIN_MS:
        raise ValueError("runtime limits: an endpoint question is waited for %d ms at the input's end "
                         "(endpoint_retire_ms) and the model call it is has %d ms (model_call_ms); the wait must "
                         "be the longer by %d ms at least"
                         % (table['endpoint_retire_ms'], table['model_call_ms'], MARGIN_MS))
    if table['separation_max_ms'] < table['separation_min_ms']:
        raise ValueError("runtime limits: a separation's budget is at least %d ms (separation_min_ms) and at most "
                         "%d ms (separation_max_ms); the most must not be the less"
                         % (table['separation_min_ms'], table['separation_max_ms']))
    if table['model_call_ms'] < table['separation_max_ms'] + MARGIN_MS:
        raise ValueError("runtime limits: a separation may take %d ms (separation_max_ms) and the model call it "
                         "runs in is given %d ms (model_call_ms); the call's must be the longer by %d ms at least"
                         % (table['separation_max_ms'], table['model_call_ms'], MARGIN_MS))
    return table


def worker_table(stated):
    table = limits_from(stated)
    whole_read = table['storage_wait_ms'] + MARGIN_MS
    exchange_write = table['host_write_ms'] + MARGIN_MS
    return {
        'exchange_read_ms': table['host_read_ms'] + MARGIN_MS,
        'exchange_write_ms': exchange_write,
        'opening_ms': table['storage_wait_ms'] + MARGIN_MS,
        'whole_read_ms': whole_read,
        'whole_publication_ms': 3 * exchange_write + whole_read,
        'drain_idle_ms': table['drain_idle_ms'],
        'reply_settings_ms': table['reply_settings_ms'],
        'abort_ms': table['abort_ms'],
        'capture_close_ms': table['capture_close_ms'],
        'session_open_ms': table['session_open_ms'],
        'opening_notice_ms': table['opening_notice_ms'],
        'audio_write_ms': table['audio_write_ms'],
        'control_write_ms': table['control_write_ms'],
        'retire_ms': table['retire_ms'],
        'capture_tail_ms': table['capture_tail_ms'],
        'model_call_ms': table['model_call_ms'],
        'input_tail_ms': table['input_tail_ms'],
        'output_take_ms': table['output_take_ms'],
        'speaker_match_ms': table['speaker_match_ms'],
        'warm_probe_ms': table['warm_probe_ms'],
        'endpoint_decision_ms': table['endpoint_decision_ms'],
        'endpoint_retire_ms': table['endpoint_retire_ms'],
        'separation_min_ms': table['separation_min_ms'],
        'separation_max_ms': table['separation_max_ms'],
    }


def worker_environment(stated):
    return json.dumps(worker_table(stated), separators=(',', ':'))


def complete_limits(stated):
    missing = [name for name in MEMBERS if name not in _named(stated)]
    if missing:
        raise ValueError('runtime limits do not state ' + ', '.join(missing)
                         + '; every member is stated, so that none is left to a compiled default')
    return limits_from(stated)


def profile_limits(profile, *, released):
    if MEMBER not in profile:
        if released:
            raise ValueError(NOT_STATED)
        return None
    return complete_limits(profile[MEMBER]) if released else limits_from(profile[MEMBER])


def read_limits_file(path):
    raw = Path(path).read_bytes()

    def once(pairs):
        members = {}
        for name, value in pairs:
            if name in members:
                raise ValueError('runtime limit ' + name + ' is stated twice')
            members[name] = value
        return members

    try:
        stated = json.loads(raw, object_pairs_hook=once)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('the limits file is not JSON: ' + str(error)) from None
    return complete_limits(stated), hashlib.sha256(raw).hexdigest()


def limits_to_state(parent, table=None):
    if table is not None:
        return complete_limits(table)
    if MEMBER in parent:
        return limits_from(parent[MEMBER])
    return default_limits()
