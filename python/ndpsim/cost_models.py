"""Pure service formulas and deterministic fit algorithms; no workload or simulator imports."""
import math
from statistics import median_low
from .static_ir import U64, canonical, digest_bytes, require, _STRICT, _SCHEMA_REGISTRY, _load
from pathlib import Path


def obj(properties, required):
    return dict(type='object', properties=properties, required=required, additionalProperties=False)


def array(items, minimum=0, maximum=1000000):
    return dict(type='array', items=items, minItems=minimum, maxItems=maximum)


U = dict(type='integer', minimum=0, maximum=U64)
N = dict(type='number', minimum=0)
NUMBER = dict(type='number')
SPLIT = obj(dict(feature=U, threshold=U, right=U), ['feature', 'threshold', 'right'])
PARAMETER_SCHEMAS = {
    'constant/1': obj(dict(ns=U), ['ns']),
    'nonnegative-linear/1': obj(dict(intercept_ns=N, coefficients=array(N)), ['intercept_ns', 'coefficients']),
    'upper-bound-table/1': obj(dict(bins=array(obj(dict(upper=U, ns=U), ['upper', 'ns']), 1)), ['bins']),
    'log-gbdt/1': obj(dict(initial=NUMBER, rate=N, trees=array(array(dict(oneOf=[NUMBER, SPLIT]), 1, 15), 1, 180)), ['initial', 'rate', 'trees']),
    'integer-rate/1': obj(dict(setup_ns=U, units_per_ns=U, weights=array(U)), ['setup_ns', 'units_per_ns', 'weights']),
}
PROFILE_SCHEMA = _load(Path(__file__).with_name('schemas')/'ndpsim-cost-profile-v1.schema.json')


def schema_hash(formula):
    require(formula in PARAMETER_SCHEMAS, 'unknown formula')
    return digest_bytes(canonical(PARAMETER_SCHEMAS[formula]))


def profile_schema(profile):
    try: _STRICT(PROFILE_SCHEMA, registry=_SCHEMA_REGISTRY).validate(profile)
    except Exception as e: raise ValueError('Profile schema: '+str(e)) from e


def rounded(value):
    require(math.isfinite(value) and 0 <= value < 2**64, 'service overflow/nonfinite/negative')
    n = math.ceil(value)
    require(n <= U64, 'rounded service overflow')
    return n


def estimate(formula, p, values):
    if formula == 'constant/1': return p['ns']
    if formula == 'nonnegative-linear/1':
        result = float(p['intercept_ns'])
        for c, v in zip(p['coefficients'], values): result += c*float(v)
        return rounded(result)
    if formula == 'upper-bound-table/1':
        for b in p['bins']:
            if values[0] <= b['upper']: return b['ns']
        raise ValueError('table feature outside domain')
    if formula == 'integer-rate/1':
        units = sum(w*v for w, v in zip(p['weights'], values))
        require(units <= U64, 'integer service overflow')
        result = p['setup_ns'] + units//p['units_per_ns'] + bool(units % p['units_per_ns'])
        require(result <= U64, 'integer setup overflow')
        return result
    require(formula == 'log-gbdt/1', 'unknown formula')
    total = 0.0
    for tree in p['trees']:
        i = 0
        while isinstance(tree[i], dict):
            n = tree[i]; i = i+1 if values[n['feature']] <= n['threshold'] else n['right']
        total += tree[i]
    return max(1, rounded(math.exp(p['initial'] + p['rate']*total)))


def validate_model(formula, parameters, ranges):
    require(formula in PARAMETER_SCHEMAS, 'unknown formula')
    _STRICT(PARAMETER_SCHEMAS[formula]).validate(parameters)
    p = parameters
    if formula == 'constant/1': return
    if formula in ('nonnegative-linear/1', 'integer-rate/1'):
        require(len(p['coefficients' if formula == 'nonnegative-linear/1' else 'weights']) == len(ranges), 'coefficient count')
        if formula == 'integer-rate/1': require(p['units_per_ns'] > 0, 'zero rate')
        estimate(formula, p, [r['maximum'] for r in ranges]); return
    if formula == 'upper-bound-table/1':
        require(len(ranges) == 1, 'table needs one feature')
        previous = ranges[0]['minimum']-1
        for b in p['bins']:
            require(previous < b['upper'] <= ranges[0]['maximum'], 'table boundary')
            previous = b['upper']
        require(previous == ranges[0]['maximum'], 'incomplete table domain'); return
    require(p['rate'] > 0, 'nonpositive GBDT rate')
    magnitude = 0.0
    for tree in p['trees']:
        def walk(i, depth):
            require(i < len(tree), 'incomplete tree')
            n = tree[i]
            if not isinstance(n, dict): return i+1
            require(depth < 3 and n['feature'] < len(ranges), 'tree depth/feature')
            r = ranges[n['feature']]
            require(r['minimum'] <= n['threshold'] < r['maximum'], 'tree threshold')
            require(walk(i+1, depth+1) == n['right'], 'tree preorder/right child')
            return walk(n['right'], depth+1)
        require(walk(0, 0) == len(tree), 'tree trailing nodes')
        magnitude += max(abs(n) for n in tree if not isinstance(n, dict))
    rounded(math.exp(p['initial'] + p['rate']*magnitude))


def quantile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values)*fraction)-1)]


def metrics(rows):
    require(rows, 'no error samples')
    errors = [abs(pred-actual) for actual, pred in rows]
    relative = [e/actual for e, (actual, _) in zip(errors, rows)]
    return dict(samples=len(rows), mae_ns=math.fsum(errors)/len(rows), relative_p50=quantile(relative, .5), relative_p90=quantile(relative, .9))


def fit(formula, rows, options, tick=lambda: None):
    """rows = [(ordered uint64 features, positive corrected_ns)]. Fixed order/ties."""
    require(rows and all(ns > 0 for _, ns in rows), 'empty/nonpositive training')
    x, y = [r[0] for r in rows], [r[1] for r in rows]
    if formula == 'constant/1': return dict(ns=median_low(y))
    if formula == 'nonnegative-linear/1':
        scale = [max(1, max(v[i] for v in x)) for i in range(len(x[0]))]
        columns = list(zip(*[(1.0, *(v/s for v, s in zip(values, scale))) for values in x]))
        gram = [[math.fsum(a*b for a, b in zip(c, d)) for d in columns] for c in columns]
        rhs = [math.fsum(a*b for a, b in zip(c, y)) for c in columns]
        coef = [0.0]*len(columns)
        for _ in range(512):
            tick()
            for i, col in enumerate(gram):
                if col[i]: coef[i] = max(0.0, (rhs[i]-sum(col[j]*coef[j] for j in range(len(coef)) if j != i))/col[i])
        return dict(intercept_ns=coef[0], coefficients=[c/s for c, s in zip(coef[1:], scale)])
    if formula == 'upper-bound-table/1':
        require(len(x[0]) == 1, 'table needs one feature')
        edges = sorted({quantile([v[0] for v in x], f) for f in (.25, .5, .75, 1)})
        return dict(bins=[dict(upper=e, ns=median_low([ns for v, ns in rows if (i == 0 or v[0] > edges[i-1]) and v[0] <= e])) for i, e in enumerate(edges)])
    require(formula == 'log-gbdt/1', 'formula has no fitter')
    require(set(options) == {'trees', 'depth', 'leaf', 'rate'}, 'GBDT options')
    require(all(type(options[k]) is int for k in ('trees', 'depth', 'leaf')) and 1 <= options['trees'] <= 180 and 1 <= options['depth'] <= 3 and options['leaf'] >= 1 and type(options['rate']) in (int, float) and 0 < options['rate'] <= 1, 'GBDT option range')
    y = [math.log(v) for v in y]; initial = math.fsum(y)/len(y); pred = [initial]*len(y); trees = []
    for _ in range(options['trees']):
        tick(); residual = [a-b for a, b in zip(y, pred)]
        def cart(indices, depth):
            tick(); mean = math.fsum(residual[i] for i in indices)/len(indices)
            if depth == 0 or len(indices) < 2*options['leaf']: return mean
            best = None; gain_best = 1e-16
            for feature in range(len(x[0])):
                tick(); ordered = sorted(indices, key=lambda i: x[i][feature]); left_sum = 0.0
                for position, i in enumerate(ordered[:-1], 1):
                    left_sum += residual[i]; right = len(indices)-position
                    if position < options['leaf'] or right < options['leaf'] or x[i][feature] == x[ordered[position]][feature]: continue
                    gain = (left_sum-position*mean)**2*len(indices)/(position*right)
                    if gain > gain_best: gain_best = gain; best = feature, x[i][feature], ordered[:position], ordered[position:]
            if best is None: return mean
            f, threshold, left, right = best
            return [f, threshold, cart(left, depth-1), cart(right, depth-1)]
        tree = cart(list(range(len(y))), options['depth'])
        def predict(t, values):
            while isinstance(t, list): t = t[2] if values[t[0]] <= t[1] else t[3]
            return t
        pred = [p+options['rate']*predict(tree, v) for p, v in zip(pred, x)]
        flat = []
        def flatten(t):
            if not isinstance(t, list): flat.append(t); return
            n = dict(feature=t[0], threshold=t[1]); flat.append(n); flatten(t[2]); n['right'] = len(flat); flatten(t[3])
        flatten(tree); trees.append(flat)
    return dict(initial=initial, rate=options['rate'], trees=trees)
