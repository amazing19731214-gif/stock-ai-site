"""
検証：トレンドが一致したときだけ予想すると、的中率は上がるか？

対象：予想タブ・個別タブ・ランキングの全銘柄（約100銘柄）、過去2年（500営業日）
期間：明日・1ヶ月後（21営業日）・3ヶ月後（63営業日）

比べる方式
- 全一致     ：短期・中期・長期のトレンドが3つとも同じ向きのときだけ予想
- 長期＋短期 ：長期と短期のトレンドが同じ向きのときだけ予想
- 強いトレンド：長期トレンドと同じ向きに、3ヶ月で10%以上動いているときだけ予想
- いつもの方式：今サイトで使っているルール（参考）

公平に比べるため、予想した「同じ日」に毎日「上がる」と言った場合の的中率と比べる。
1ヶ月後・3ヶ月後は、隣り合う日の予想がほぼ同じ期間を見ているため、
実質的なサンプル数は「日数 ÷ 期間」として偶然の範囲かどうかを判定する。

結果：experiments/trend_filter.json
"""
import json
import os

import numpy as np
import pandas as pd

import build_data as B

TEST_DAYS = 500


def trend_states(close: pd.Series) -> pd.DataFrame:
    ma = {n: close.rolling(n).mean() for n in (5, 25, 75, 200)}
    def state(up, down):
        return pd.Series(np.where(up, 1, np.where(down, -1, 0)), index=close.index)
    short = state((close > ma[25]) & (ma[5] > ma[25]), (close < ma[25]) & (ma[5] < ma[25]))
    mid = state((close > ma[75]) & (ma[25] > ma[75]), (close < ma[75]) & (ma[25] < ma[75]))
    long = state((close > ma[200]) & (ma[75] > ma[200]), (close < ma[200]) & (ma[75] < ma[200]))
    mom63 = close / close.shift(63) - 1
    valid = ma[200].notna() & mom63.notna()
    df = pd.DataFrame({"short": short, "mid": mid, "long": long, "mom63": mom63})
    return df[valid]


def strategies(st: pd.DataFrame, close: pd.Series) -> dict:
    all3 = np.where((st.short == 1) & (st.mid == 1) & (st.long == 1), 1,
                    np.where((st.short == -1) & (st.mid == -1) & (st.long == -1), -1, 0))
    ls = np.where((st.long == 1) & (st.short == 1), 1,
                  np.where((st.long == -1) & (st.short == -1), -1, 0))
    strong = np.where((st.long == 1) & (st.mom63 > 0.10), 1,
                      np.where((st.long == -1) & (st.mom63 < -0.10), -1, 0))
    out = {
        "全一致": pd.Series(all3, index=st.index),
        "長期＋短期": pd.Series(ls, index=st.index),
        "強いトレンド": pd.Series(strong, index=st.index),
    }
    hs = B.horizon_scores(close)
    out["いつもの方式"] = {k: np.sign(v).reindex(st.index) for k, v in hs.items()}
    return out


def main():
    tickers = {}
    for name, t in B.FORECAST_TARGETS.items():
        tickers[t] = name
    for m in B.DIAG_TARGETS.values():
        for name, t in m.items():
            tickers[t] = name
    tickers.update(B.JP_STOCKS)
    tickers.update(B.US_STOCKS)

    close_all = B.get_closes(list(tickers), "5y")
    rows = []  # 銘柄・日・期間ごとの (方式, 予想, 実際に上がったか)
    used = 0
    for t in tickers:
        if t not in close_all:
            continue
        c = close_all[t].dropna()
        if len(c) < 400:
            continue
        used += 1
        st = trend_states(c)
        strat = strategies(st, c)
        for key, _, days in B.HORIZONS:
            fut = (c.shift(-days) / c - 1).reindex(st.index)
            known = fut.notna()
            idx = st.index[known][-TEST_DAYS:]
            up = (fut.loc[idx] > 0).values
            for sname, sig in strat.items():
                s = (sig[key] if isinstance(sig, dict) else sig).reindex(idx).fillna(0).values
                rows.append(pd.DataFrame({"h": key, "s": sname, "sig": s, "up": up}))
    df = pd.concat(rows, ignore_index=True)

    days_of = {k: d for k, _, d in B.HORIZONS}
    result = {"tickers": used, "test_days": TEST_DAYS, "by_horizon": {}}
    for key, label, days in B.HORIZONS:
        sub = df[df.h == key]
        base_all = float(sub[sub.s == "全一致"].up.mean())
        res = {"label": label, "always_up_all_days": round(base_all * 100, 1), "strategies": {}}
        for sname in ["全一致", "長期＋短期", "強いトレンド", "いつもの方式"]:
            x = sub[sub.s == sname]
            called = x[x.sig != 0]
            total = len(x)
            if not len(called):
                continue
            hit = ((called.sig > 0) & called.up) | ((called.sig < 0) & ~called.up)
            hit_rate = float(hit.mean())
            base_same = float(called.up.mean())  # 予想した同じ日に毎日「上がる」と言った場合
            n_eff = max(len(called) / max(days, 1) / 5, 1)  # 重なりと銘柄間の連動をざっくり考慮
            se = np.sqrt(base_same * (1 - base_same) / n_eff)
            edge = hit_rate - base_same
            upc, dnc = called[called.sig > 0], called[called.sig < 0]
            res["strategies"][sname] = {
                "coverage": round(len(called) / total * 100, 1),
                "calls": int(len(called)),
                "hit_rate": round(hit_rate * 100, 1),
                "always_up_same_days": round(base_same * 100, 1),
                "edge": round(edge * 100, 1),
                "significant": bool(edge > 2 * se),
                "up_calls": int(len(upc)),
                "up_hit": round(float(upc.up.mean()) * 100, 1) if len(upc) else None,
                "down_calls": int(len(dnc)),
                "down_hit": round(float((~dnc.up).mean()) * 100, 1) if len(dnc) else None,
            }
        result["by_horizon"][key] = res

    os.makedirs("experiments", exist_ok=True)
    result["updated_at"] = B.now_jst()
    with open("experiments/trend_filter.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
