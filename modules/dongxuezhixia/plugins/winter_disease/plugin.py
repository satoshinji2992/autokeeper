def _g(o, k, d=None):
    if isinstance(o, dict):
        return o.get(k, d)
    return d

def _num(v, d):
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return d

def _phase(c):
    c = _num(c, 0)
    if c >= 81:
        return 1
    if c >= 31:
        return 2
    return 3

def _find(items, who):
    for it in items:
        if _g(it, 'id') == who:
            return it
    return None

def _con(inv):
    ch = _g(inv, 'characteristics')
    if isinstance(ch, dict):
        v = ch.get('体质')
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return None

def _infect(state, ctx, effects):
    invs = _g(ctx, 'investigators', [])
    if not isinstance(invs, list):
        invs = []
    infected = state.get('infected')
    if not isinstance(infected, list):
        infected = []
        state['infected'] = infected
    ph = _num(state.get('phase'), 1)
    if ph >= 3:
        target = 2
    elif ph == 2:
        target = 1
    else:
        target = 0
    have = 0
    for who in infected:
        if isinstance(who, str) and who:
            have = have + 1
    if have >= target:
        return
    for inv in invs:
        if have >= target:
            break
        if '死亡' in _g(inv, 'conditions', []):
            continue
        iid = _g(inv, 'id')
        if not isinstance(iid, str) or not iid:
            continue
        if iid in infected:
            continue
        infected.append(iid)
        have = have + 1
        effects.append({'kind': 'condition', 'who': iid, 'key': '患病', 'amount': 1})

def _tick(state, ctx, api, minutes):
    effects = []
    rolls = _num(state.get('rolls'), 0)
    if rolls <= 0:
        base = 1
    elif rolls == 1:
        base = 2
    else:
        base = 3
    mit = _num(state.get('mitigation'), 0)
    if mit < 0:
        mit = 0
    if mit > 2:
        mit = 2
    count = base - mit
    if count < 1:
        count = 1
    if count > 3:
        count = 3
    values = []
    raw = api.roll(str(count) + 'D10')
    if isinstance(raw, list):
        for d in raw:
            if isinstance(d, int) and not isinstance(d, bool):
                values.append(d)
    if not values:
        values = [1]
    total = 0
    high = values[0]
    low = values[0]
    for d in values:
        total = total + d
        if d > high:
            high = d
        if d < low:
            low = d
    old = _num(state.get('counter'), 0)
    counter = old - total
    if counter < 0:
        counter = 0
    state['counter'] = counter
    state['rolls'] = rolls + 1
    state['last_tick'] = minutes
    op = _num(state.get('phase'), _phase(old))
    np = _phase(counter)
    if np < op:
        np = op
    if np >= 2 and op < 2:
        effects.append({'kind': 'flag', 'key': 'disease_phase2'})
    if np >= 3 and op < 3:
        effects.append({'kind': 'flag', 'key': 'disease_phase3'})
    state['phase'] = np
    if counter == 0 and not state.get('counter_zero'):
        state['counter_zero'] = True
        effects.append({'kind': 'flag', 'key': 'counter_zero'})
    invs = _g(ctx, 'investigators', [])
    if not isinstance(invs, list):
        invs = []
    infected = state.get('infected')
    if not isinstance(infected, list):
        infected = []
        state['infected'] = infected
    for who in infected:
        if not isinstance(who, str) or not who:
            continue
        member = _find(invs, who)
        if member is None or '死亡' in _g(member, 'conditions', []):
            continue
        passed = False
        res = api.check(who, '体质', '常规')
        if isinstance(res, dict):
            passed = bool(res.get('passed'))
        loss = low if passed else high
        effects.append({'kind': 'attribute', 'who': who, 'key': '体质', 'amount': -loss})
        if not passed:
            effects.append({'kind': 'sanity', 'who': who})
        con = _con(member)
        if con is not None and con - loss <= 0:
            effects.append({'kind': 'condition', 'who': who, 'key': '死亡', 'amount': 1})
    _infect(state, ctx, effects)
    return {'state': state, 'effects': effects}

def handle(event, ctx, api):
    hook = _g(event, 'hook', '')
    raw = _g(ctx, 'state', {})
    state = dict(raw) if isinstance(raw, dict) else {}
    if not isinstance(state.get('infected'), list):
        state['infected'] = []
    if not isinstance(state.get('mitigation'), int) or isinstance(state.get('mitigation'), bool):
        state['mitigation'] = 0
    if not isinstance(state.get('rolls'), int) or isinstance(state.get('rolls'), bool):
        state['rolls'] = 0
    minutes = _num(_g(ctx, 'minutes', 0), 0)
    turn = _num(_g(ctx, 'turn', 0), 0)
    if state.get('cadence') not in ['daily', 'dramatic']:
        state['cadence'] = 'daily'
    if hook == 'start':
        c0 = _num(state.get('counter'), None)
        if c0 is None:
            total = 0
            raw_dice = api.roll('2D10')
            if isinstance(raw_dice, list):
                for d in raw_dice:
                    if isinstance(d, int) and not isinstance(d, bool):
                        total = total + d
            counter = 100 - total
            if counter < 0:
                counter = 0
            state['counter'] = counter
            state['rolls'] = 0
            state['last_tick'] = minutes
            phase = _phase(counter)
            state['phase'] = phase
            effects = []
            if phase >= 2:
                effects.append({'kind': 'flag', 'key': 'disease_phase2'})
            if phase >= 3:
                effects.append({'kind': 'flag', 'key': 'disease_phase3'})
            if counter == 0:
                state['counter_zero'] = True
                effects.append({'kind': 'flag', 'key': 'counter_zero'})
            _infect(state, ctx, effects)
            return {'state': state, 'effects': effects}
        return {'state': state, 'effects': []}
    if hook == 'action':
        action = _g(event, 'action', '')
        args = _g(event, 'arguments', {})
        if not isinstance(args, dict):
            args = {}
        if action == 'mitigate':
            red = args.get('reduction')
            if isinstance(red, bool) or not isinstance(red, int):
                return {'state': state, 'effects': []}
            if red < 1 or red > 2:
                return {'state': state, 'effects': []}
            mit = _num(state.get('mitigation'), 0)
            if mit < 0:
                mit = 0
            if mit >= 2:
                return {'state': state, 'effects': []}
            state['mitigation'] = min(2, mit + red)
            return {'state': state, 'effects': []}
        if action == 'dramatic_tick':
            if state.get('cadence') != 'dramatic':
                return {'state': state, 'effects': []}
            if _num(state.get('last_dramatic_turn'), -1) == turn:
                return {'state': state, 'effects': []}
            if _num(state.get('counter'), 0) <= 0:
                return {'state': state, 'effects': []}
            state['last_dramatic_turn'] = turn
            return _tick(state, ctx, api, minutes)
        return {'state': state, 'effects': []}
    if hook == 'turn':
        if state.get('cadence') != 'daily':
            return {'state': state, 'effects': []}
        c2 = _num(state.get('counter'), None)
        if c2 is None:
            state['last_tick'] = minutes
            return {'state': state, 'effects': []}
        if c2 <= 0:
            return {'state': state, 'effects': []}
        last = _num(state.get('last_tick'), None)
        if last is None:
            state['last_tick'] = minutes
            return {'state': state, 'effects': []}
        if minutes - last < 1440:
            return {'state': state, 'effects': []}
        output = _tick(state, ctx, api, last + 1440)
        output['repeat'] = minutes - state['last_tick'] >= 1440 and state['counter'] > 0
        return output
    return {'state': state, 'effects': []}
