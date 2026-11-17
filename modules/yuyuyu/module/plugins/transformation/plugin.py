PLACES = ['ruan_home', 'huang_home', 'library', 'music_square', 'night_market', 'shipowei', 'drain_tunnel']

def handle(event, ctx, api):
    state = dict(ctx['state'])
    seen = list(state.get('seen', []))
    levels = dict(state.get('levels', {}))
    fails = state.get('gate_fails', 0)
    effects = []
    hook = event.get('hook')
    if hook == 'visit':
        loc = ctx.get('location')
        if loc in PLACES and loc not in seen:
            seen.append(loc)
            if len(seen) >= 2 and not state.get('ready', False):
                state['ready'] = True
                effects.append({'kind': 'flag', 'key': 'outbreak_ready'})
    elif hook == 'action':
        args = event.get('arguments') or {}
        name = event.get('action')
        if name == 'contact_water':
            who = args.get('who') or ctx.get('acting')
            ids = [p.get('id') for p in ctx.get('investigators', [])]
            if who in ids:
                result = api.check(who, '体质', '常规')
                if not result['passed']:
                    level = levels.get(who, 0) + 1
                    levels[who] = level
                    if level == 1:
                        effects.append({'kind': 'condition', 'who': who, 'key': '皮肤湿润', 'amount': 1})
                        if not state.get('transforming', False):
                            state['transforming'] = True
                            effects.append({'kind': 'flag', 'key': 'pc_transforming'})
                    if level == 3:
                        effects.append({'kind': 'condition', 'who': who, 'key': '听见大海的呼唤', 'amount': 1})
                        effects.append({'kind': 'hint', 'text': '这名调查员开始隐约听见大海的呼唤。'})
        elif name == 'gate_fail':
            fails = fails + 1
            effects.append({'kind': 'hint', 'text': '这次封堵没有成功，又额外耗去了一小时。'})
            if args.get('patrol') == True and fails % 3 == 0:
                roll = api.roll('1D3')
                if roll[0] == 1:
                    effects.append({'kind': 'hint', 'text': '通道深处，一只巡逻的深潜者朝闸门口过来了。'})
    state['seen'] = seen
    state['levels'] = levels
    state['gate_fails'] = fails
    return {'state': state, 'effects': effects}
