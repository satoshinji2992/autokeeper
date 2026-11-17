ROPES = ['左舷挂钩', '右舷挂钩', '船尾固定栓']

def has_flag(ctx, key):
    flags = ctx.get('flags') or []
    if isinstance(flags, dict):
        return bool(flags.get(key))
    return key in flags

def clip(n, low, high):
    if n < low:
        return low
    if n > high:
        return high
    return n

def to_int(v):
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip('-').isdigit():
        return int(v.strip())
    return 0

def status_of(state):
    if len(state['cut']) == 0 and state['engine_tries'] == 0:
        return ''
    if state['escaped']:
        return '渔船已冲出礁石滩'
    return '网绳已割断 ' + str(len(state['cut'])) + '/3；引擎启动尝试 ' + str(state['engine_tries']) + ' 次'

def done(state, effects):
    out = {'state': state, 'effects': effects}
    s = status_of(state)
    if s:
        out['status'] = s
    return out

def handle(event, ctx, api):
    state = dict(ctx.get('state') or {})
    state['seasick'] = list(state.get('seasick') or [])
    state['rested'] = list(state.get('rested') or [])
    state['cut'] = list(state.get('cut') or [])
    state['engine_tries'] = to_int(state.get('engine_tries', 0))
    state['incense'] = bool(state.get('incense', False))
    state['statue'] = bool(state.get('statue', False))
    state['escaped'] = bool(state.get('escaped', False))
    if event.get('hook') != 'action':
        return done(state, [])
    action = event.get('action') or ''
    args = event.get('arguments') or {}
    who = args.get('who') or ctx.get('acting') or ''
    ids = [inv.get('id') for inv in (ctx.get('investigators') or [])]
    if action in ['seasick', 'rest', 'recover', 'cut_rope', 'start_engine'] and who not in ids:
        return done(state, [])
    if action == 'seasick':
        bonus = clip(to_int(args.get('bonus', 0)), 0, 1)
        if who in state['rested']:
            bonus = bonus + 1
            state['rested'] = [x for x in state['rested'] if x != who]
        result = api.check(who, '体质', '常规', bonus=clip(bonus, 0, 2))
        if result.get('passed'):
            return done(state, [])
        effects = []
        if who not in state['seasick']:
            state['seasick'].append(who)
            effects.append({'kind': 'condition', 'who': who, 'key': '晕船', 'amount': 1})
        return done(state, effects)
    if action == 'rest':
        if not has_flag(ctx, 'at_reef'):
            return done(state, [])
        if who in state['seasick'] and who not in state['rested']:
            state['rested'].append(who)
        return done(state, [])
    if action == 'recover':
        if who not in state['seasick']:
            return done(state, [])
        state['seasick'] = [x for x in state['seasick'] if x != who]
        return done(state, [{'kind': 'condition', 'who': who, 'key': '晕船', 'amount': -1}])
    if action == 'cut_rope':
        if not has_flag(ctx, 'net_cast') or state['escaped']:
            return done(state, [])
        rope = args.get('rope') or ''
        if rope not in ROPES or rope in state['cut']:
            return done(state, [])
        bonus = clip(to_int(args.get('bonus', 0)), 0, 2)
        result = api.check(who, '敏捷', '常规', bonus=bonus)
        if not result.get('passed'):
            return done(state, [])
        state['cut'].append(rope)
        text = rope + '上的网绳被割断了。'
        if len(state['cut']) == 2:
            text = text + '船身的倾斜缓解了一些。'
        return done(state, [{'kind': 'note', 'text': text}])
    if action == 'start_engine':
        if not has_flag(ctx, 'net_cast') or state['escaped'] or len(state['cut']) < 2:
            return done(state, [])
        skill = args.get('skill') or '驾驶(小型船舶)'
        bonus = clip(to_int(args.get('bonus', 0)), -2, 2)
        state['engine_tries'] = state['engine_tries'] + 1
        result = api.check(who, skill, '常规', bonus=bonus)
        if not result.get('passed'):
            return done(state, [])
        state['escaped'] = True
        return done(state, [{'kind': 'note', 'text': '引擎轰鸣起来，螺旋桨绞断了剩下的网绳。'}, {'kind': 'flag', 'key': 'escaped_reef'}])
    if action == 'calm':
        if not has_flag(ctx, 'net_cast') or state['escaped']:
            return done(state, [])
        method = args.get('method') or ''
        if method == 'incense' and not state['incense']:
            state['incense'] = True
            return done(state, [{'kind': 'note', 'text': '香头的青烟飘过船舷，周围的海和尚迟疑了，这一轮停止了动作。'}])
        if method == 'statue' and not state['statue']:
            state['statue'] = True
            return done(state, [{'kind': 'note', 'text': '木雕小像落进海里，周围的海和尚迟疑了，这一轮停止了动作。'}])
        return done(state, [])
    return done(state, [])
