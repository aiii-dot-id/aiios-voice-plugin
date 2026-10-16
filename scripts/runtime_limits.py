"""The time limits a native set's runtime profile states: its "limits" member.

The Go carrier owns the table (plugin/native/limits.go): its numbers of
milliseconds, their defaults, their ranges and the rules by which they nest.
It reads them from the signed profile voice-runtime.json and takes its
compiled default for any the profile leaves out. A profile that leaves them
out therefore ships limits that are stated nowhere in its package. So the
scripts that put an engine into a native set write every member into its
profile, and staging and assembly, where a checkpoint becomes a release,
refuse a profile that does not state them.

This module is the scripts' one statement of that table. It is a second
statement of what the carrier compiles, in another language, and only
tests/test_runtime_limits.py keeps the two equal: that test reads the
carrier's source for the member names and their order, the defaults, the
ranges, the host's allowance and the margin, and holds limits_from to the
tables the carrier's own tests accept and refuse.

One rule here is not the carrier's, because the carrier is not told the
number it needs: a set's wait for its worker's readiness ends inside what the
set declares to the host for its start (startup_covers_readiness).

Every check here raises; none is an `assert`, so none is lost under python -O.
"""
import hashlib
import json
from pathlib import Path

# The member of voice-runtime.json that holds the table.
MEMBER = 'limits'

# Each member's default, floor and ceiling in milliseconds, in the order the
# carrier declares them. A profile is written in this order.
_TABLE = (
    ('host_read_ms', 5000, 250, 120000),
    ('host_write_ms', 12000, 250, 120000),
    ('storage_wait_ms', 12000, 250, 120000),
    ('drain_idle_ms', 15000, 250, 120000),
    # Time a listener waits for the first sound: a range of its own.
    ('reply_settings_ms', 150, 10, 2000),
    ('abort_ms', 5000, 250, 120000),
    ('capture_close_ms', 45000, 250, 120000),
    ('session_open_ms', 60000, 250, 120000),
    ('opening_notice_ms', 1500, 250, 120000),
    # A control's answer, and one write of audio to the host's pipe.
    ('control_ms', 2000, 250, 120000),
    ('audio_write_ms', 3000, 250, 120000),
    # The worker's readiness, counted from the carrier's own start: a model
    # load, with a range of its own. The default sits inside the 180000 a set
    # declares for its start (startup_covers_readiness).
    ('ready_ms', 175000, 1000, 3600000),
    # One write of a line from the worker to the carrier.
    ('control_write_ms', 3000, 250, 120000),
    # The worker's own end once it has begun to end, and the frames of an
    # enrollment capture up to the sample it was told it ends at.
    ('retire_ms', 5000, 250, 120000),
    ('capture_tail_ms', 2000, 250, 120000),
    # The carrier's end: its wait for the worker's exit, which is the
    # worker's own time to retire and the margin at least; its wait to see a
    # killed worker gone; and the host's lane taking the last events.
    ('worker_exit_ms', 5500, 250, 120000),
    ('worker_reap_ms', 5000, 250, 120000),
    ('lane_flush_ms', 2000, 250, 120000),
    # The engine's own waits, which the worker hands on to it: one model call,
    # a conversation's last frames, synthesized audio waiting to be taken, a
    # final's wait for its speaker, the warm inference of a start, and below
    # them the endpoint's two and the separation's two.
    ('model_call_ms', 30000, 250, 120000),
    ('input_tail_ms', 3000, 250, 120000),
    ('output_take_ms', 15000, 250, 120000),
    ('speaker_match_ms', 15000, 250, 120000),
    ('warm_probe_ms', 40000, 250, 120000),
    # The endpoint model's verdict at the point where a pause would end a
    # turn, a listener's wait with the reply's range; and each of its
    # questions still unanswered when the input ends, which is a model call
    # and is given that call's time and the margin at least.
    ('endpoint_decision_ms', 1000, 10, 2000),
    ('endpoint_retire_ms', 30500, 250, 120000),
    # The two bounds of the budget one separation of competing talkers has;
    # a model call is given the longer and the margin at least.
    ('separation_min_ms', 4000, 250, 120000),
    ('separation_max_ms', 25000, 250, 120000),
)
MEMBERS = tuple(row[0] for row in _TABLE)
DEFAULTS = {row[0]: row[1] for row in _TABLE}
RANGES = {row[0]: (row[2], row[3]) for row in _TABLE}

# How long the host waits for one invocation of a plugin. It is the host's
# number: a table whose storage control would outlast it is refused.
HOST_INVOKE_ALLOWANCE_MS = 90000
# How much longer an outer waiter gives an inner one, so the inner answers first.
MARGIN_MS = 500
# What something the carrier says is given to reach the host inside the
# host's allowance for it: an answer inside an invocation's, and the word that
# its worker did not report ready inside what its set declares for a start.
# A margin of the arithmetic, like the one above.
HOST_ANSWER_MARGIN_MS = 2000

NOT_STATED = ('this runtime profile states no time limits (the "limits" member of voice-runtime.json), '
              'so the limits it would ship with are stated nowhere in its package; rebuild its checkpoint '
              'with a script that states them')


def default_limits():
    """Every member's default, as a profile states them."""
    return dict(DEFAULTS)


def storage_operation_ms(table):
    """What the carrier gives a control that uses the private files.

    The carrier's storageOperation: twice a whole read, one whole publication
    (two stages, the publish and the read that verifies it, each exchange
    given the margin) and what any control is given for its own answer.
    """
    whole_read = table['storage_wait_ms'] + MARGIN_MS
    whole_publication = 3 * (table['host_write_ms'] + MARGIN_MS) + whole_read
    return 2 * whole_read + whole_publication + table['control_ms']


def playback_report_ms(table):
    """What the carrier gives a playback report (its playbackReport).

    The worker holds a report until the audio write it counts has ended, and
    gives that write audio_write_ms; then it answers as any control.
    """
    return table['audio_write_ms'] + table['control_ms']


def startup_covers_readiness(table, startup_ms):
    """Refuse a table whose wait for readiness does not end inside its set's declared start.

    startup_ms is what a set of the package declares to the host as the
    allowance for its start. The carrier waits ready_ms, counted from its own
    start, for its worker to report ready, and when that passes it says so,
    with the number. That wait and the margin for the carrier's word must end
    inside the allowance, so that what was waited for is said before the
    allowance has passed.

    This rule is the scripts' alone. The carrier is not told what its set
    declares, so it cannot refuse such a table: the package's assembly, the
    one place where a set's profile and its declaration are both in hand,
    applies it.
    """
    if type(startup_ms) is not int:
        raise ValueError('startup_ms must be a whole number of milliseconds')
    if table['ready_ms'] + HOST_ANSWER_MARGIN_MS > startup_ms:
        raise ValueError('runtime limits: the carrier waits %d ms for its worker to report ready (ready_ms), and '
                         'its set declares %d ms to the host for its start (startup_ms); the wait and %d ms for '
                         'the carrier to say that it passed must end inside what the set declares'
                         % (table['ready_ms'], startup_ms, HOST_ANSWER_MARGIN_MS))


def _named(stated):
    """What is stated, once it is an object that names only limits the carrier knows.

    A name the carrier does not know is a limit somebody meant to set and
    did not: it is refused before anything else is said about the table.
    """
    if type(stated) is not dict:
        raise ValueError('runtime limits must be an object of whole milliseconds by name')
    unknown = sorted(str(name) for name in stated if name not in DEFAULTS)
    if unknown:
        raise ValueError('runtime limit ' + unknown[0] + ' is not one the carrier knows')
    return stated


def limits_from(stated):
    """The table the carrier waits by for what a profile states, or a refusal.

    As the carrier reads it: a member that is left out takes its default,
    and a table that is out of range or does not nest is refused, in the
    carrier's own sentence. A member the carrier does not know, and one that
    is not a whole number, are refused too, as the carrier refuses them.
    """
    table = dict(DEFAULTS)
    for name, value in _named(stated).items():
        # A bool is an int to Python and is not a number of milliseconds.
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
    # The carrier's wait for its worker's exit is the outer of the worker's
    # own time to retire: by the margin, never by an equal number.
    if table['worker_exit_ms'] < table['retire_ms'] + MARGIN_MS:
        raise ValueError('runtime limits: this carrier would wait %d ms for its worker to exit and the worker has '
                         '%d ms to retire; the wait must be the longer by %d ms at least'
                         % (table['worker_exit_ms'], table['retire_ms'], MARGIN_MS))
    # The engine's waits nest with the ones around them, each by the margin:
    # its wait for its audio to be taken is the outer of one write of that
    # audio to the host; a drain's idle limit is the outer of the wait for a
    # conversation's last frames; and the worker's readiness is the outer of
    # the warm inference of its start.
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
    # An endpoint question unanswered at the input's end is a model call in
    # flight, and a separation runs inside one: the wait for the first is the
    # outer of a model call, and a model call the outer of the second.
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
    """What the carrier hands the worker in AII_VOICE_LIMITS for what a profile states.

    The carrier's forWorker: an exchange is the host's time for it and the
    margin; the opening and a whole read are the storage's whole wait and the
    margin; a whole publication is two stages, the publish and the read that
    verifies it; the rest pass through as stated. control_ms, ready_ms and
    the three waits of the carrier's own end (worker_exit_ms, worker_reap_ms,
    lane_flush_ms) are the carrier's alone and are not handed over.
    """
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
    """The value of AII_VOICE_LIMITS as the carrier writes it: one JSON object, no white space."""
    return json.dumps(worker_table(stated), separators=(',', ':'))


def complete_limits(stated):
    """A table that states every member and holds, in the carrier's order.

    What a released profile and a caller's table must be. With a member left
    out, the limit in force would be whatever the carrier compiled, and the
    package would not say it.
    """
    missing = [name for name in MEMBERS if name not in _named(stated)]
    if missing:
        raise ValueError('runtime limits do not state ' + ', '.join(missing)
                         + '; every member is stated, so that none is left to a compiled default')
    return limits_from(stated)


def profile_limits(profile, *, released):
    """The table a parsed profile states, checked; None where it states none and may.

    released: the profile is on its way into a package, and must state every
    member. Elsewhere a profile from before the table was stated is still
    read (a parent to rebuild from, an old stage to recover), and a table
    that it does state must still hold.
    """
    if MEMBER not in profile:
        if released:
            raise ValueError(NOT_STATED)
        return None
    return complete_limits(profile[MEMBER]) if released else limits_from(profile[MEMBER])


def read_limits_file(path):
    """A caller's table from a JSON file, and the SHA-256 of the bytes it was read from.

    The file states every member, each once. The digest is of the bytes that
    were parsed, so a script that binds its inputs binds this table and not a
    later edit of the file.
    """
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
    """What the profile of a set built from a parent states.

    The caller's table where one is given. Otherwise every member the parent
    states, exactly as the parent states it: a rebuild does not move a limit
    nobody asked it to move. A member the parent does not state is written
    at its default, which is what the carrier built here would wait by
    anyway: the parent is from before that member was added, or from before
    the table was stated at all. Every member is written out either way.
    """
    if table is not None:
        return complete_limits(table)
    if MEMBER in parent:
        return limits_from(parent[MEMBER])
    return default_limits()
