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
    state['engine_round'] = str(state.get('engine_round') or '')
    state['round_tries'] = clip(to_int(state.get('round_tries', 0)), 0, 2)
    state['incense'] = bool(state.get('incense', False))
    state['statue'] = bool(state.get('statue', False))
    state['escaped'] = bool(state.get('escaped', False))
    if event.get('hook') != 'action':
        return done(state, [])
    action = event.get('action') or ''
    args = event.get('arguments') or {}
    who = args.get('who') or ctx.get('acting') or ''
    investigators = {inv.get('id'): inv for inv in (ctx.get('investigators') or [])}
    if action in ['seasick', 'rest', 'recover', 'cut_rope', 'start_engine', 'calm'] and who not in investigators:
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
        if who != ctx.get('acting') or investigators[who].get('location') != 'boat_cabin':
            return done(state, [])
        encounter = ctx.get('encounter') or {}
        round_key = encounter['id'] + ':' + str(encounter['round']) if encounter else 'turn:' + str(ctx.get('turn'))
        if state['engine_round'] != round_key:
            state['engine_round'] = round_key
            state['round_tries'] = 0
        skill = args.get('skill') or '驾驶(小型船舶)'
        bonus = clip(to_int(args.get('bonus', 0)), -2, 2)
        for _ in range(2 - state['round_tries']):
            state['engine_tries'] += 1
            state['round_tries'] += 1
            if api.check(who, skill, '常规', bonus=bonus).get('passed'):
                state['escaped'] = True
                return done(state, [
                    {'kind': 'note', 'text': '引擎轰鸣起来，螺旋桨绞断了剩下的网绳。'},
                    {'kind': 'flag', 'key': 'escaped_reef'}
                ])
        return done(state, [])
    if action == 'calm':
        if not has_flag(ctx, 'net_cast') or state['escaped'] or who != ctx.get('acting'):
            return done(state, [])
        words = {'incense': '香头的青烟飘过船舷', 'statue': '木雕小像落进海里'}
        method = args.get('method') or ''
        if method not in words or state[method]:
            return done(state, [])
        participants = (ctx.get('encounter') or {}).get('participants') or []
        monks = [
            p['id'] for p in participants
            if p.get('id') == 'sea_monk' and p.get('side') == 'foe' and p.get('presence') == 'in'
        ]
        if not monks:
            return done(state, [])
        state[method] = True
        effects = [{'kind': 'lose_action', 'who': monk, 'text': '迟疑着停住'} for monk in monks]
        effects.append({'kind': 'note', 'text': words[method] + '，海和尚迟疑着停住。'})
        return done(state, effects)
    return done(state, [])
