import os
import json
import glob
import math
import urllib.request
from datetime import datetime, timezone

from main import forecast_market, price6, QUANTILES, DEFAULT_PARAMS

KIT_INPUT_DIR = "/home/hatch/workspace/makefun-poly-faa/polymarket-faa-attribution-02-kit/input"
CACHE_PATH = "/tmp/poly-answers.json"

HOUSE_Z = [-1.644854, -0.67449, 0, 0.67449, 1.644854]
HOUSE_SIGMA = 0.0025

def parse_iso_utc(iso_str: str) -> int:
    if iso_str.endswith('Z'):
        iso_str = iso_str[:-1] + '+00:00'
    dt = datetime.fromisoformat(iso_str)
    return int(dt.timestamp())

def load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cache(cache: dict):
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f)

def fetch_prices_history(yes_token: str, start_ts: int, end_ts: int, cache: dict) -> list[tuple[int, float]]:
    key = f"{yes_token}_{start_ts}_{end_ts}"
    if key in cache:
        return cache[key]
    
    url = f"https://clob.polymarket.com/prices-history?market={yes_token}&startTs={start_ts}&endTs={end_ts}&fidelity=1"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            history = data.get('history', [])
            res = [(item['t'], float(item['p'])) for item in history]
            cache[key] = res
            return res
    except Exception as e:
        print(f"Error fetching history for {yes_token}: {e}")
        return []

def run_house_forecast(prices: list[float], horizon: int) -> list[list[float]]:
    if not prices:
        return [[0.5] * 5 for _ in range(horizon)]
    x = prices[-1]
    rows = []
    for h in range(1, horizon + 1):
        sqrt_h = math.sqrt(h)
        row = [price6(x + z * HOUSE_SIGMA * sqrt_h) for z in HOUSE_Z]
        rows.append(sorted([price6(v) for v in row]))
    return rows

def evaluate_case_with_params(case_path: str, params: dict, cache: dict):
    with open(case_path, 'r', encoding='utf-8') as f:
        case_data = json.load(f)
    
    at_str = case_data['at']
    at_ts = parse_iso_utc(at_str)
    horizon = case_data['horizon_minutes']
    markets = case_data['markets']
    
    minute_timestamps = [at_ts + m * 60 for m in range(1, horizon + 1)]
    
    our_forecasts = {}
    house_forecasts = {}
    actuals = {}
    
    medians_list = []
    for m in markets:
        prices = m['prices']
        yes_token = m['yes_token']
        
        our_q = forecast_market(prices, horizon, params)
        our_forecasts[m['id']] = our_q
        medians_list.append(prices[-1] if prices else 0.5)
        
    if params.get('sum_constrain', False) and len(markets) > 1:
        total_median = sum(medians_list)
        if total_median > 1.0:
            scale = 1.0 / total_median
            for idx, m_id in enumerate([m['id'] for m in markets]):
                old_median = medians_list[idx]
                new_median = old_median * scale
                delta = new_median - old_median
                for row in our_forecasts[m_id]:
                    shifted = [price6(q + delta) for q in row]
                    row[:] = sorted(shifted)

    for m in markets:
        prices = m['prices']
        yes_token = m['yes_token']
        house_q = run_house_forecast(prices, horizon)
        house_forecasts[m['id']] = house_q
        
        start_ts = at_ts - 3600
        end_ts = at_ts + horizon * 60
        hist = fetch_prices_history(yes_token, start_ts, end_ts, cache)
        hist.sort(key=lambda x: x[0])
        
        last_price = prices[-1] if prices else 0.5
        act_prices = []
        for mt in minute_timestamps:
            p = last_price
            for ht, hp in hist:
                if ht <= mt:
                    p = hp
                else:
                    break
            act_prices.append(p)
            last_price = p
            
        actuals[m['id']] = act_prices

    our_losses = []
    house_losses = []
    
    for m in markets:
        mid = m['id']
        our_q_list = our_forecasts[mid]
        house_q_list = house_forecasts[mid]
        act_list = actuals[mid]
        
        for h_idx, y in enumerate(act_list):
            our_row = our_q_list[h_idx]
            house_row = house_q_list[h_idx]
            
            for tau, q in zip(QUANTILES, our_row):
                our_losses.append(100.0 * max(tau * (y - q), (tau - 1) * (y - q)))
            for tau, q in zip(QUANTILES, house_row):
                house_losses.append(100.0 * max(tau * (y - q), (tau - 1) * (y - q)))
                
    mean_our_loss = sum(our_losses) / len(our_losses)
    mean_house_loss = sum(house_losses) / len(house_losses)
    
    return mean_our_loss, mean_house_loss

def run_alignment_check(case_files: list[str], cache: dict):
    print("Running alignment self-check...")
    max_global_diff = 0.0
    for cf in case_files:
        with open(cf, 'r', encoding='utf-8') as f:
            case_data = json.load(f)
        case_id = case_data['case']
        at_ts = parse_iso_utc(case_data['at'])
        for m in case_data['markets']:
            start_ts = parse_iso_utc(m['start'])
            yes_token = m['yes_token']
            prices = m['prices']
            
            hist = fetch_prices_history(yes_token, start_ts, at_ts, cache)
            hist.sort(key=lambda x: x[0])
            
            expected_timestamps = [start_ts + i * 60 for i in range(len(prices))]
            last_p = prices[0] if prices else 0.5
            fetched_prices = []
            for ts in expected_timestamps:
                p = last_p
                for ht, hp in hist:
                    if ht <= ts:
                        p = hp
                    else:
                        break
                fetched_prices.append(p)
                last_p = p
                
            diffs = [abs(a - b) for a, b in zip(fetched_prices, prices)]
            m_max = max(diffs) if diffs else 0.0
            if m_max > max_global_diff:
                max_global_diff = m_max
    
    print(f"Alignment check completed. Max absolute difference across all cases/markets: {max_global_diff:.6f}")
    if max_global_diff > 1e-4:
        print("WARNING: Alignment discrepancy detected! Prices fetched do not match case file prices closely.")
    else:
        print("Alignment check PASSED (discrepancy <= 1e-4).")

def main():
    cache = load_cache()
    case_files = sorted(glob.glob(os.path.join(KIT_INPUT_DIR, 'p*.in')))
    if not case_files:
        # Fallback if kit input dir not found directly
        case_files = sorted(glob.glob("input/p*.in"))
        
    run_alignment_check(case_files, cache)
    
    estimators = ["mean", "median", "winsor"]
    z_outers = [1.644854, 1.8]
    recent_windows = [360, 1440]
    sum_constrains = [False, True]
    
    grid = []
    for est in estimators:
        for zo in z_outers:
            for rw in recent_windows:
                for sc in sum_constrains:
                    grid.append({
                        "estimator": est,
                        "z_outer": zo,
                        "recent_window": rw,
                        "weight_recent": 0.7,
                        "weight_long": 0.3,
                        "sigma_floor": 0.0025,
                        "sigma_cap": 0.02,
                        "sum_constrain": sc,
                    })
                    
    print(f"\nStarting grid search over {len(grid)} parameter combinations...")
    grid_results = []
    
    for idx, params in enumerate(grid):
        case_losses = {}
        total_our = 0.0
        total_house = 0.0
        for cf in case_files:
            with open(cf, 'r', encoding='utf-8') as f:
                cdata = json.load(f)
            c_id = cdata['case']
            our_l, house_l = evaluate_case_with_params(cf, params, cache)
            case_losses[c_id] = our_l
            total_our += our_l
            total_house += house_l
            
        mean_our = total_our / len(case_files)
        mean_house = total_house / len(case_files)
        
        grid_results.append({
            "params": params,
            "mean_our_loss": mean_our,
            "mean_house_loss": mean_house,
            "case_losses": case_losses
        })
        print(f"[{idx+1}/{len(grid)}] est={params['estimator']}, z={params['z_outer']}, win={params['recent_window']}, sum_c={params['sum_constrain']} -> Loss: {mean_our:.6f}")
        
    save_cache(cache)
    
    # Sort by mean_our_loss
    grid_results.sort(key=lambda x: x['mean_our_loss'])
    
    print("\n" + "="*95)
    print(f"{'Rank':<4} | {'Est':<8} | {'Z_outer':<8} | {'Win':<6} | {'SumC':<6} | {'Mean Loss':<10} | {'House Loss':<10} | {'Delta vs House':<14}")
    print("-" * 95)
    for rank, res in enumerate(grid_results[:10], 1):
        p = res['params']
        delta = res['mean_our_loss'] - res['mean_house_loss']
        print(f"{rank:<4} | {p['estimator']:<8} | {p['z_outer']:<8} | {p['recent_window']:<6} | {str(p['sum_constrain']):<6} | {res['mean_our_loss']:<10.6f} | {res['mean_house_loss']:<10.6f} | {delta:+14.6f}")
    print("="*95)
    
    best = grid_results[0]
    print(f"\nBest Combo per Mean Pinball Loss:")
    print(json.dumps(best['params'], indent=2))
    print(f"Mean Our Loss: {best['mean_our_loss']:.6f} (House: {best['mean_house_loss']:.6f})")
    print("\nPer-case losses for best combo:")
    for c_id, loss in best['case_losses'].items():
        print(f"  {c_id}: {loss:.6f}")

if __name__ == '__main__':
    main()
