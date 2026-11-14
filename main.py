import os
import json
import math
import glob

QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
DEFAULT_PARAMS = {
    "estimator": "winsor",
    "z_outer": 1.644854,
    "recent_window": 1440,
    "weight_recent": 0.7,
    "weight_long": 0.3,
    "sigma_floor": 0.0025,
    "sigma_cap": 0.02,
    "sum_constrain": True,
}

def price6(x: float) -> float:
    return round(min(1.0, max(0.0, x)), 6) + 0.0

def compute_sigma(prices: list[float], params: dict) -> float:
    floor = params.get("sigma_floor", 0.0025)
    cap = params.get("sigma_cap", 0.02)
    if len(prices) < 2:
        return floor
    
    diffs = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    estimator = params.get("estimator", "mean")
    
    def estimate_from_diffs(d_list):
        if not d_list:
            return 0.0
        if estimator == "median":
            sd = sorted(d_list)
            n = len(sd)
            if n == 0:
                return 0.0
            med = sd[n // 2] if n % 2 == 1 else (sd[n // 2 - 1] + sd[n // 2]) / 2.0
            return med / 0.6744897501960817
        elif estimator == "winsor":
            capped = [min(d, 0.01) for d in d_list]
            return (sum(capped) / len(capped)) / 0.7978845608028654
        else: # "mean"
            return (sum(d_list) / len(d_list)) / 0.7978845608028654

    long_sigma = estimate_from_diffs(diffs)
    
    recent_window = params.get("recent_window", 360)
    recent_diffs = diffs[-recent_window:] if len(diffs) >= recent_window else diffs
    recent_sigma = estimate_from_diffs(recent_diffs)
    
    weight_recent = params.get("weight_recent", 0.7)
    weight_long = params.get("weight_long", 0.3)
    
    blended = weight_recent * recent_sigma + weight_long * long_sigma
    return max(floor, min(cap, blended))

def forecast_market(prices: list[float], horizon: int, params: dict | None = None) -> list[list[float]]:
    if params is None:
        params = DEFAULT_PARAMS
    
    if not prices:
        return [[0.5] * 5 for _ in range(horizon)]
    
    x = prices[-1]
    sigma = compute_sigma(prices, params)
    z_outer = params.get("z_outer", 1.644854)
    z = [-z_outer, -0.67449, 0, 0.67449, z_outer]
    
    rows = []
    for h in range(1, horizon + 1):
        sqrt_h = math.sqrt(h)
        row = []
        for zv in z:
            val = price6(x + zv * sigma * sqrt_h)
            row.append(val)
        row = sorted([price6(v) for v in row])
        rows.append(row)
    return rows

def process_case(filepath: str, output_dir: str, params: dict | None = None):
    if params is None:
        params = DEFAULT_PARAMS
    with open(filepath, 'r', encoding='utf-8') as f:
        case_data = json.load(f)
    
    horizon = case_data.get('horizon_minutes', 240)
    markets = case_data.get('markets', [])
    
    market_forecasts = []
    medians_list = []
    
    for m in markets:
        prices = m.get('prices', [])
        q_rows = forecast_market(prices, horizon, params)
        market_forecasts.append({'id': m['id'], 'q': q_rows})
        medians_list.append(prices[-1] if prices else 0.5)
        
    if params.get('sum_constrain', False) and len(markets) > 1:
        total_median = sum(medians_list)
        if total_median > 1.0:
            scale = 1.0 / total_median
            for idx, m_f in enumerate(market_forecasts):
                old_median = medians_list[idx]
                new_median = old_median * scale
                delta = new_median - old_median
                for row in m_f['q']:
                    shifted = [price6(q + delta) for q in row]
                    row[:] = sorted(shifted)
                    
    output_obj = {'forecasts': market_forecasts}
    case_id = case_data.get('case', os.path.basename(filepath).replace('.in', ''))
    out_path = os.path.join(output_dir, f"{case_id}.out")
    
    os.makedirs(output_dir, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output_obj, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')

def main():
    input_dir = os.environ.get('MAKEFUN_INPUT_DIR', '/input')
    output_dir = os.environ.get('MAKEFUN_OUTPUT_DIR', '/output')
    
    in_files = sorted(glob.glob(os.path.join(input_dir, '*.in')))
    for filepath in in_files:
        process_case(filepath, output_dir, DEFAULT_PARAMS)

if __name__ == '__main__':
    main()
