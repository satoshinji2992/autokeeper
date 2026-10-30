def handle(event, ctx, api):
    state = dict(ctx.get('state') or {})
    state['escapes'] = state.get('escapes', 0)
    state['hide_retry'] = dict(state.get('hide_retry') or {})
    state['night_phase'] = state.get('night_phase', 'until_midnight')
    effects = []
    if event.get('hook') != 'action':
        return {'state': state, 'effects': effects}
    act = event.get('action')
    args = event.get('arguments') or {}
    who = args.get('who', '')
    if act == 'hide_move':
        skill = args.get('skill')
        if skill not in ('潜行', '侦查', '攀爬') or not who:
            return {'state': state, 'effects': effects}
        r = api.check(who, skill, '常规')
        retry = state['hide_retry']
        if r['passed']:
            retry.pop(who, None)
            effects.append({'kind': 'note', 'text': '检定成功，到达了隐蔽处。'})
        elif not retry.get(who):
            retry[who] = True
            effects.append({'kind': 'note', 'text': '检定失败：前方出现险情。可以放弃，也可以再检定一次。'})
        else:
            retry.pop(who, None)
            effects.append({'kind': 'note', 'text': '再次失败，麻烦了。'})
        state['hide_retry'] = retry
    elif act == 'night_luck':
        if not who or state['night_phase'] == 'found':
            return {'state': state, 'effects': effects}
        r = api.check(who, '幸运', '常规')
        if r['passed']:
            state['night_phase'] = 'hourly'
            effects.append({'kind': 'note', 'text': '这一小时平安无事。'})
        else:
            state['night_phase'] = 'found'
            effects.append({'kind': 'note', 'text': '有普通镇民也跑到这里避难，身后引来了有福者。'})
    elif act == 'kill_blessed':
        if not who:
            return {'state': state, 'effects': effects}
        together = args.get('together') is True
        r = api.check(who, '幸运', '常规')
        if together:
            if r['passed']:
                state['escapes'] = state['escapes'] + 1
                effects.append({'kind': 'note', 'text': '混乱中出现了一个空当，攒下一次脱身机会。'})
            else:
                effects.append({'kind': 'note', 'text': '又一名有福者扑了上来。'})
        else:
            if r['passed']:
                effects.append({'kind': 'note', 'text': '在场只有这一名有福者，可以继续任务。'})
            else:
                effects.append({'kind': 'note', 'text': '又一名有福者扑了上来。'})
    elif act == 'dodge_escape':
        if not who or args.get('dodge_passed') is not True:
            return {'state': state, 'effects': effects}
        r = api.check(who, '幸运', '常规')
        if r['passed']:
            effects.append({'kind': 'note', 'text': '趁乱脱身，可以继续任务。'})
        else:
            effects.append({'kind': 'note', 'text': '没能脱身，仍在战斗中。'})
    elif act == 'use_escape':
        n = args.get('count')
        if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > state['escapes']:
            return {'state': state, 'effects': effects}
        state['escapes'] = state['escapes'] - n
        effects.append({'kind': 'note', 'text': '用掉 ' + str(n) + ' 次脱身机会，相应人数的调查员脱离了战斗。'})
    elif act == 'sniper':
        if not who:
            return {'state': state, 'effects': effects}
        r = api.check(who, '幸运', '常规')
        if r['passed']:
            effects.append({'kind': 'note', 'text': '屋顶的狙击手没注意到你。'})
        else:
            d = api.roll('1D6')[0]
            effects.append({'kind': 'damage', 'who': who, 'amount': d})
            effects.append({'kind': 'note', 'text': '屋顶传来一声枪响，子弹擦伤了你。'})
    return {'state': state, 'effects': effects}
