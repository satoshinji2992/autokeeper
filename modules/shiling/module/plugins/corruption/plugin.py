THRESHOLDS = [10, 20, 25, 30, 35, 40, 60]
ENV = {'adamson_home': 3, 'adamson_bedroom': 3, 'peter_office': 3, 'bar': 5, 'bar_cellar': 5, 'greenwood_mansion': 10, 'mansion_storage': 10, 'mansion_study': 10, 'mansion_playroom': 10, 'jim_bedroom': 15}

def unreal(loc):
    return loc.startswith('theatre') or loc.startswith('other_')

def handle(event, ctx, api):
    state = dict(ctx['state'])
    ind = state.get('individual', 1)
    minutes = ctx.get('minutes', 0) or 0
    day = minutes // 1440
    last = state.get('day', 0)
    if day > last:
        ind = ind + 3 * (day - last)
        state['day'] = day
    if event.get('hook') == 'action' and event.get('action') == 'corrupt':
        args = event.get('arguments') or {}
        amt = args.get('amount', 0)
        if isinstance(amt, int) and amt >= -20 and amt <= 20:
            ind = ind + amt
            if ind < 0:
                ind = 0
    loc = ctx.get('location') or ''
    if unreal(loc):
        env = 30
        timec = 0
    else:
        env = ENV.get(loc, 0)
        hour = (minutes // 60) % 24
        timec = 10 if (hour >= 18 or hour < 6) else 0
    total = ind + env + timec
    state['individual'] = ind
    state['total'] = total
    reached = list(state.get('reached', []))
    flags = ctx.get('flags') or []
    effects = []
    for t in THRESHOLDS:
        key = 'corr_' + str(t)
        if total >= t and key not in reached:
            reached.append(key)
            if key not in flags:
                effects.append({'kind': 'flag', 'key': key})
    state['reached'] = reached
    return {'state': state, 'effects': effects}
