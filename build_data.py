"""
サイト用データをまとめて作るスクリプト（GitHub Actions で毎日実行）

作るファイル（data/ フォルダ）
- forecast.json       … ドル円・日経平均・米国指数の「明日・1ヶ月後・3ヶ月後」予想
- forecast_log.json   … 明日予想の記録（実績の的中率に使う）
- predictions.json    … AI診断（日本株・米国株の個別銘柄の3期間予想）
- ranking.json        … 値上がり・値下がりランキング（日本株・米国株）
- value_ranking.json  … 割安株ランキング（日本株・米国株）
- yutai_ranking.json  … 株主優待ランキング（yutai.json を手入力）
"""
import json
import os
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

JST = timezone(timedelta(hours=9))
DATA = "data"

# ---- 対象の設定 ---------------------------------------------------------

# 予想タブ（詳しく表示）
FORECAST_TARGETS = {
    "ドル/円": "JPY=X",
    "日経平均": "^N225",
    "S&P500": "^GSPC",
    "NASDAQ総合": "^IXIC",
}

# AI診断タブ（個別銘柄）  市場: {表示名: ティッカー}
DIAG_TARGETS = {
    "jp": {
        "トヨタ自動車": "7203.T", "ソニーグループ": "6758.T", "三菱UFJ FG": "8306.T",
        "任天堂": "7974.T", "東京エレクトロン": "8035.T", "ソフトバンクグループ": "9984.T",
    },
    "us": {
        "アップル": "AAPL", "エヌビディア": "NVDA", "マイクロソフト": "MSFT",
        "アマゾン": "AMZN", "テスラ": "TSLA", "アルファベット": "GOOGL",
    },
}

# ランキングの対象（主要な大型株）
JP_STOCKS = {
    "7203.T": "トヨタ自動車", "6758.T": "ソニーグループ", "9984.T": "ソフトバンクグループ",
    "6861.T": "キーエンス", "8306.T": "三菱UFJ FG", "9432.T": "NTT",
    "9433.T": "KDDI", "6098.T": "リクルートHD", "8035.T": "東京エレクトロン",
    "4063.T": "信越化学工業", "9983.T": "ファーストリテイリング", "7974.T": "任天堂",
    "6501.T": "日立製作所", "8058.T": "三菱商事", "8031.T": "三井物産",
    "8001.T": "伊藤忠商事", "4502.T": "武田薬品工業", "6902.T": "デンソー",
    "7267.T": "本田技研工業", "6367.T": "ダイキン工業", "4568.T": "第一三共",
    "8316.T": "三井住友FG", "8411.T": "みずほFG", "6981.T": "村田製作所",
    "6954.T": "ファナック", "7741.T": "HOYA", "4661.T": "オリエンタルランド",
    "2914.T": "日本たばこ産業", "9434.T": "ソフトバンク", "6594.T": "ニデック",
    "6273.T": "SMC", "4519.T": "中外製薬", "7011.T": "三菱重工業",
    "6857.T": "アドバンテスト", "8766.T": "東京海上HD", "3382.T": "セブン&アイHD",
    "2802.T": "味の素", "9020.T": "JR東日本", "9022.T": "JR東海",
    "8801.T": "三井不動産", "5401.T": "日本製鉄", "7201.T": "日産自動車",
    "7751.T": "キヤノン", "6752.T": "パナソニックHD", "4452.T": "花王",
}
US_STOCKS = {
    "AAPL": "アップル", "MSFT": "マイクロソフト", "NVDA": "エヌビディア",
    "AMZN": "アマゾン", "GOOGL": "アルファベット", "META": "メタ",
    "TSLA": "テスラ", "BRK-B": "バークシャー・ハサウェイ", "JPM": "JPモルガン",
    "V": "ビザ", "MA": "マスターカード", "UNH": "ユナイテッドヘルス",
    "XOM": "エクソンモービル", "JNJ": "ジョンソン・エンド・ジョンソン", "WMT": "ウォルマート",
    "PG": "P&G", "HD": "ホーム・デポ", "COST": "コストコ",
    "AVGO": "ブロードコム", "ORCL": "オラクル", "LLY": "イーライリリー",
    "KO": "コカ・コーラ", "PEP": "ペプシコ", "BAC": "バンク・オブ・アメリカ",
    "NFLX": "ネットフリックス", "AMD": "AMD", "ADBE": "アドビ",
    "CRM": "セールスフォース", "CSCO": "シスコシステムズ", "INTC": "インテル",
    "DIS": "ウォルト・ディズニー", "MCD": "マクドナルド", "NKE": "ナイキ",
    "PFE": "ファイザー", "MRK": "メルク", "ABBV": "アッヴィ",
    "CVX": "シェブロン", "T": "AT&T", "VZ": "ベライゾン",
    "QCOM": "クアルコム", "IBM": "IBM", "BA": "ボーイング",
}
MARKETS = {"jp": JP_STOCKS, "us": US_STOCKS}

# 予想の期間  (キー, 表示名, 営業日数)
HORIZONS = [("1d", "明日", 1), ("1m", "1ヶ月後", 21), ("3m", "3ヶ月後", 63)]


# ---- 共通 ---------------------------------------------------------------

def now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def load_json(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(name, obj):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def get_closes(tickers, period):
    """終値の表（列=ティッカー）を返す"""
    if isinstance(tickers, str):
        tickers = [tickers]
    df = yf.download(tickers, period=period, interval="1d",
                     progress=False, auto_adjust=True, threads=True)
    close = df["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    return close


def code_of(ticker):
    return ticker.replace(".T", "")


def rnd(v):
    """値の大きさに合わせて丸める"""
    v = float(v)
    return round(v, 3) if abs(v) < 100 else round(v, 2) if abs(v) < 10000 else round(v, 1)


def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    diff = close.diff()
    gain = diff.clip(lower=0).rolling(period).mean()
    loss = (-diff.clip(upper=0)).rolling(period).mean()
    return 100 - 100 / (1 + gain / loss)


# ---- チャート予想（明日・1ヶ月・3ヶ月） --------------------------------

def horizon_scores(close: pd.Series) -> dict:
    """各日の時点で、各期間の「上か下か」スコア（-4〜+4）を計算する"""
    ma5 = close.rolling(5).mean()
    ma25 = close.rolling(25).mean()
    ma75 = close.rolling(75).mean()
    ma200 = close.rolling(200).mean()
    r = rsi_series(close)
    mom5 = close / close.shift(5) - 1
    mom63 = close / close.shift(63) - 1
    mom126 = close / close.shift(126) - 1
    sg = np.sign

    s1d = (sg(ma5 - ma25) + sg(close - ma25)
           + (r <= 30).astype(int) - (r >= 70).astype(int)
           + (mom5 > 0.01).astype(int) - (mom5 < -0.01).astype(int))
    s1m = sg(close - ma25) + sg(ma25 - ma75) + sg(ma25 - ma25.shift(5)) + sg(mom63)
    s3m = sg(close - ma75) + sg(ma75 - ma200) + sg(ma75 - ma75.shift(20)) + sg(mom126)
    return {
        "1d": s1d.where(ma25.notna() & r.notna()),
        "1m": s1m.where(ma75.notna() & mom63.notna()),
        "3m": s3m.where(ma200.notna() & mom126.notna()),
    }


def horizon_reasons(key, close: pd.Series):
    c = close.iloc[-1]
    ma = lambda n: close.rolling(n).mean().iloc[-1]
    mom = lambda n: (c / close.iloc[-n - 1] - 1) * 100
    pos = lambda a, b: "上" if a > b else "下"
    if key == "1d":
        r = rsi_series(close).iloc[-1]
        return [
            f"5日平均が25日平均より{pos(ma(5), ma(25))}",
            f"現在値が25日平均より{pos(c, ma(25))}",
            f"RSI {r:.0f}" + ("（買われすぎ気味）" if r >= 70 else "（売られすぎ気味）" if r <= 30 else "（中立圏）"),
            f"直近5日の値動き {mom(5):+.1f}%",
        ]
    if key == "1m":
        slope = close.rolling(25).mean()
        return [
            f"現在値が25日平均より{pos(c, ma(25))}",
            f"25日平均が75日平均より{pos(ma(25), ma(75))}",
            "25日平均の向きが" + ("上向き" if slope.iloc[-1] > slope.iloc[-6] else "下向き"),
            f"直近3ヶ月の値動き {mom(63):+.1f}%",
        ]
    slope = close.rolling(75).mean()
    return [
        f"現在値が75日平均より{pos(c, ma(75))}",
        f"75日平均が200日平均より{pos(ma(75), ma(200))}",
        "75日平均の向きが" + ("上向き" if slope.iloc[-1] > slope.iloc[-21] else "下向き"),
        f"直近6ヶ月の値動き {mom(126):+.1f}%",
    ]


def backtest(score: pd.Series, close: pd.Series, days: int, dates=None, window: int = 500):
    """同じルールで過去に毎日予想していたら、何%当たったか
    dates を渡すと、その日付だけで検証する（AIと同じ期間で比べるため）"""
    future = close.shift(-days) / close - 1
    df = pd.DataFrame({"s": score, "f": future}).dropna()
    df = df.loc[df.index.intersection(dates)] if dates is not None else df.tail(window)
    if not len(df):
        return {"days": 0, "hit_rate": None, "always_up_rate": None}
    called = df[df.s != 0]
    hits = ((called.s > 0) & (called.f > 0)) | ((called.s < 0) & (called.f < 0))
    return {
        "days": int(len(called)),
        "hit_rate": round(float(hits.mean()) * 100, 1) if len(called) else None,
        "always_up_rate": round(float((df.f > 0).mean()) * 100, 1),
    }


def direction_of(score):
    if score is None or not np.isfinite(score):
        return "データ不足", ""
    if score == 0:
        return "どちらとも言えない", ""
    strength = "強め" if abs(score) >= 3 else "弱め"
    return ("上昇" if score > 0 else "下落"), strength


# ---- AI予想（機械学習：ロジスティック回帰） ------------------------------
#
# 過去の値動きから作った特徴（いくつかの期間の騰落率、移動平均からの乖離、
# RSI、値動きの大きさ）と、「その後◯日で上がったか」の関係を学習し、
# 上がる確率を出す。
#
# 検証は「ウォークフォワード」方式：ある日の予想には、その日までに答えが
# 分かっていたデータだけで学習したモデルを使う（未来の情報を使わない）。
# モデルは21営業日ごとに作り直す。

ML_TEST_DAYS = 500   # 検証する日数（約2年）
ML_RETRAIN = 21      # 学習し直す間隔（約1ヶ月）
ML_MIN_TRAIN = 150   # 学習に最低限必要な日数
ML_MARGIN = 0.03     # 上昇確率が47〜53%のときは「どちらとも言えない」


def ml_features(close: pd.Series) -> pd.DataFrame:
    f = pd.DataFrame(index=close.index)
    for n in (1, 5, 21, 63, 126):
        f[f"ret{n}"] = close / close.shift(n) - 1          # n日間の騰落率
    for n in (5, 25, 75, 200):
        f[f"ma{n}"] = close / close.rolling(n).mean() - 1  # 移動平均からの乖離
    ret = close.pct_change()
    f["rsi"] = rsi_series(close) / 100 - 0.5
    f["vol20"] = ret.rolling(20).std()                      # 最近の値動きの大きさ
    f["vol_change"] = ret.rolling(20).std() / ret.rolling(120).std()
    return f


def _new_model():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=1000))


def _fit_predict(X_train, y_train, X_test):
    if y_train.nunique() < 2:  # 学習データが全部「上」など
        return np.full(len(X_test), float(y_train.iloc[0]))
    m = _new_model()
    m.fit(X_train, y_train)
    return m.predict_proba(X_test)[:, 1]


def ml_call(p):
    if p is None:
        return "データ不足", ""
    if abs(p - 0.5) < ML_MARGIN:
        return "どちらとも言えない", ""
    return ("上昇" if p > 0.5 else "下落"), ("強め" if abs(p - 0.5) >= 0.1 else "弱め")


def ml_horizon(feats: pd.DataFrame, close: pd.Series, days: int):
    future = close.shift(-days) / close - 1
    valid = feats.notna().all(axis=1)
    X, fut = feats[valid], future[valid]
    labeled = fut.notna().values
    y = (fut > 0).astype(int)
    n = len(X)
    lab_pos = np.where(labeled)[0]
    test_pos = lab_pos[lab_pos >= ML_MIN_TRAIN + days][-ML_TEST_DAYS:]
    if len(test_pos) < 40:
        return None

    # ウォークフォワード検証
    probs = pd.Series(np.nan, index=X.index)
    for i in range(0, len(test_pos), ML_RETRAIN):
        block = test_pos[i:i + ML_RETRAIN]
        train = np.arange(0, block[0] - days + 1)  # この時点で答えが分かっている行だけ
        probs.iloc[block] = _fit_predict(X.iloc[train], y.iloc[train], X.iloc[block])
    t = probs.dropna()
    up = t > 0.5 + ML_MARGIN
    down = t < 0.5 - ML_MARGIN
    called = up | down
    actual_up = fut.loc[t.index] > 0
    hits = (up & actual_up) | (down & ~actual_up)

    # 今日の予想：答えが分かっている全データで学習
    p_now = None
    if X.index[-1] == close.index[-1]:
        train = lab_pos
        p_now = float(_fit_predict(X.iloc[train], y.iloc[train], X.iloc[[n - 1]])[0])
    direction, strength = ml_call(p_now)
    return {
        "direction": direction, "strength": strength,
        "prob_up": round(p_now * 100, 1) if p_now is not None else None,
        "backtest": {
            "days": int(called.sum()),
            "hit_rate": round(float(hits[called].mean()) * 100, 1) if called.any() else None,
        },
        "_test_dates": t.index,
    }


def compute_horizons(close: pd.Series, with_reasons=True):
    scores = horizon_scores(close)
    feats = ml_features(close)
    price = float(close.iloc[-1])
    sigma = float(close.pct_change().tail(60).std())
    out = []
    for key, label, days in HORIZONS:
        s = scores[key]
        now = float(s.iloc[-1]) if pd.notna(s.iloc[-1]) else None
        direction, strength = direction_of(now)
        width = sigma * np.sqrt(days)  # 値動きの大きさ × √期間
        try:
            ml = ml_horizon(feats, close, days)
        except Exception as e:
            print(f"  AI予想エラー({label}): {e}")
            ml = None
        dates = ml.pop("_test_dates") if ml else None
        h = {
            "key": key, "label": label, "direction": direction, "strength": strength,
            "score": int(now) if now is not None else None,
            "range_low": rnd(price * (1 - width)), "range_high": rnd(price * (1 + width)),
            "backtest": backtest(s, close, days, dates=dates),
            "ml": ml or {"direction": "データ不足", "strength": "", "prob_up": None,
                         "backtest": {"days": 0, "hit_rate": None}},
        }
        if with_reasons and now is not None:
            h["reasons"] = horizon_reasons(key, close)
        out.append(h)
    return out


# ---- 予想タブ -----------------------------------------------------------

def build_forecast():
    log = load_json("forecast_log.json", [])
    items, closes = [], {}

    for name, ticker in FORECAST_TARGETS.items():
        try:
            close = get_closes(ticker, "5y")[ticker].dropna()
            if len(close) < 260:
                raise ValueError("データ不足")
            price = float(close.iloc[-1])
            data_date = close.index[-1].strftime("%Y-%m-%d")
            horizons = compute_horizons(close)

            # 明日予想を記録（同じ日のものは追加しない）
            tomorrow = horizons[0]["direction"]
            if tomorrow in ("上昇", "下落") and not any(
                    e["name"] == name and e["data_date"] == data_date for e in log):
                log.append({"name": name, "data_date": data_date, "base": rnd(price),
                            "direction": tomorrow, "result": None})

            closes[name] = close
            items.append({
                "name": name, "ticker": ticker, "data_date": data_date,
                "price": rnd(price),
                "change_pct": round((price / float(close.iloc[-2]) - 1) * 100, 2),
                "horizons": horizons,
                "history": [rnd(v) for v in close.tail(130)],
            })
            print(f"[予想] OK {name}: " + " / ".join(f"{h['label']}={h['direction']}" for h in horizons))
        except Exception as e:
            print(f"[予想] NG {name}: {e}")
            items.append({"name": name, "ticker": ticker, "error": str(e)})

    # 記録した明日予想の答え合わせ
    for e in log:
        if e["result"] is not None or e["name"] not in closes:
            continue
        c = closes[e["name"]]
        after = c[c.index > pd.Timestamp(e["data_date"])]
        if len(after):
            actual = float(after.iloc[0])
            e["actual"] = rnd(actual)
            e["result"] = "的中" if ((actual > e["base"]) == (e["direction"] == "上昇")) else "はずれ"
    log = log[-800:]
    save_json("forecast_log.json", log)

    for it in items:
        done = [e for e in log if e["name"] == it["name"] and e["result"]]
        it["live"] = {"days": len(done), "hits": sum(e["result"] == "的中" for e in done),
                      "recent": done[-10:][::-1]}
    save_json("forecast.json", {"updated_at": now_jst(), "items": items})


# ---- AI診断タブ（個別銘柄） ---------------------------------------------

def build_diagnosis():
    out = {}
    for market, targets in DIAG_TARGETS.items():
        items = []
        try:
            close_all = get_closes(list(targets.values()), "5y")
        except Exception as e:
            print(f"[診断] NG {market}: {e}")
            close_all = pd.DataFrame()
        for name, ticker in targets.items():
            try:
                close = close_all[ticker].dropna()
                if len(close) < 260:
                    raise ValueError("データ不足")
                price = float(close.iloc[-1])
                items.append({
                    "name": name, "ticker": ticker, "code": code_of(ticker),
                    "price": rnd(price),
                    "change_pct": round((price / float(close.iloc[-2]) - 1) * 100, 2),
                    "horizons": compute_horizons(close, with_reasons=False),
                    "history": [rnd(v) for v in close.tail(130)],
                })
                print(f"[診断] OK {name}")
            except Exception as e:
                print(f"[診断] NG {name}: {e}")
                items.append({"name": name, "ticker": ticker, "code": code_of(ticker), "error": str(e)})
        out[market] = items
    save_json("predictions.json", {"updated_at": now_jst(), "markets": out})


# ---- 値上がりランキング -------------------------------------------------

def ranking_for(stocks: dict):
    tickers = list(stocks)
    rows = {"day": [], "ytd": [], "year": []}
    data_date = None
    try:
        close = get_closes(tickers, "2y")
        last_date = close.dropna(how="all").index[-1]
        data_date = last_date.strftime("%Y-%m-%d")
        year_start = pd.Timestamp(year=last_date.year, month=1, day=1)
        one_year_ago = last_date - pd.Timedelta(days=365)

        for t in tickers:
            if t not in close:
                continue
            c = close[t].dropna()
            if len(c) < 2:
                continue
            price = float(c.iloc[-1])
            base = {"code": code_of(t), "name": stocks[t], "price": rnd(price)}

            def add(period, ref_price, ref_date=None):
                r = dict(base, change_pct=round((price / ref_price - 1) * 100, 2))
                if ref_date is not None:
                    r["ref_price"] = rnd(ref_price)
                    r["ref_date"] = ref_date.strftime("%Y-%m-%d")
                rows[period].append(r)

            add("day", float(c.iloc[-2]))
            before_year = c[c.index < year_start]  # 前年最終日の終値が基準
            if len(before_year):
                add("ytd", float(before_year.iloc[-1]), before_year.index[-1])
            year_ago = c[c.index <= one_year_ago]  # 1年前の終値が基準
            if len(year_ago):
                add("year", float(year_ago.iloc[-1]), year_ago.index[-1])
    except Exception as e:
        print(f"[ランキング] NG: {e}")

    periods = {}
    for k, r in rows.items():
        r.sort(key=lambda x: x["change_pct"], reverse=True)
        periods[k] = {"count": len(r), "gainers": r[:10], "losers": r[::-1][:10]}
    return {"data_date": data_date, "periods": periods}


def build_ranking():
    markets = {m: ranking_for(s) for m, s in MARKETS.items()}
    save_json("ranking.json", {"updated_at": now_jst(), "markets": markets})
    for m, d in markets.items():
        print(f"[ランキング] {m}: " + " / ".join(f"{k}:{v['count']}" for k, v in d["periods"].items()))


# ---- 割安株ランキング（PER・PBR） ---------------------------------------

SECTOR_JA = {
    "Technology": "テクノロジー", "Financial Services": "金融",
    "Consumer Cyclical": "一般消費財", "Consumer Defensive": "生活必需品",
    "Industrials": "資本財", "Healthcare": "ヘルスケア",
    "Communication Services": "通信", "Basic Materials": "素材",
    "Real Estate": "不動産", "Energy": "エネルギー", "Utilities": "公益",
}


def _num(v):
    try:
        v = float(v)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _percentile_low(values):
    """小さいほど高得点（0〜100）になる順位スコア"""
    s = pd.Series(values, dtype="float64")
    n = s.notna().sum()
    if n <= 1:
        return pd.Series(np.where(s.notna(), 100.0, np.nan), index=s.index)
    return (n - s.rank(method="average")) / (n - 1) * 100


def value_for(stocks: dict):
    rows = []
    for t, name in stocks.items():
        try:
            info = yf.Ticker(t).info or {}
        except Exception as e:
            print(f"[割安] NG {name}: {e}")
            info = {}
        price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
        per = _num(info.get("trailingPE"))
        pbr = _num(info.get("priceToBook"))
        div = _num(info.get("dividendRate"))
        rows.append({
            "code": code_of(t), "name": name,
            "sector": SECTOR_JA.get(info.get("sector"), info.get("sector") or "-"),
            "price": rnd(price) if price else None,
            # PERがマイナス（赤字）や極端な値は比較から外す
            "per": round(per, 1) if per and 0 < per < 200 else None,
            "pbr": round(pbr, 2) if pbr and 0 < pbr < 100 else None,
            "div_yield": round(div / price * 100, 2) if div and price else None,
        })

    df = pd.DataFrame(rows)
    df["per_score"] = _percentile_low(df["per"])
    df["pbr_score"] = _percentile_low(df["pbr"])
    both = df["per_score"].notna() & df["pbr_score"].notna()
    df["value_score"] = ((df["per_score"] + df["pbr_score"]) / 2).where(both).round(0)

    items = []
    for r in df.drop(columns=["per_score", "pbr_score"]).to_dict("records"):
        r = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in r.items()}
        if r["value_score"] is not None:
            r["value_score"] = int(r["value_score"])
        items.append(r)
    scored = sum(1 for r in items if r["value_score"] is not None)
    return {"count": len(items), "scored": scored, "items": items}


def build_value():
    markets = {m: value_for(s) for m, s in MARKETS.items()}
    save_json("value_ranking.json", {"updated_at": now_jst(), "markets": markets})
    for m, d in markets.items():
        print(f"[割安] {m}: {d['count']}銘柄（PER・PBRとも取得 {d['scored']}）")


# ---- 注目株（ウォッチリスト＋出来高急増） -------------------------------

def get_ohlcv(tickers, period):
    if isinstance(tickers, str):
        tickers = [tickers]
    df = yf.download(tickers, period=period, interval="1d",
                     progress=False, auto_adjust=True, threads=True)
    close, vol = df["Close"], df["Volume"]
    if isinstance(close, pd.Series):
        close, vol = close.to_frame(tickers[0]), vol.to_frame(tickers[0])
    return close, vol


def watch_item(name, ticker, c: pd.Series, v: pd.Series, reason):
    price = float(c.iloc[-1])
    v20 = float(v.iloc[-21:-1].mean()) if len(v) > 21 else None
    vol_ratio = round(float(v.iloc[-1]) / v20, 1) if v20 else None
    return {
        "name": name, "ticker": ticker, "code": code_of(ticker), "reason": reason,
        "market": "jp" if ticker.endswith(".T") else "us",
        "price": rnd(price),
        "change_pct": round((price / float(c.iloc[-2]) - 1) * 100, 2),
        "change5_pct": round((price / float(c.iloc[-6]) - 1) * 100, 2) if len(c) > 6 else None,
        "vol_ratio": vol_ratio,
        "listed_days": int(len(c)),
        "horizons": compute_horizons(c, with_reasons=False),
        "history": [rnd(x) for x in c.tail(130)],
    }


def build_watch():
    """data/watchlist.json の銘柄と、出来高が急に増えた銘柄の予想"""
    watch = load_json("watchlist.json", [])
    watch_tickers = [w["ticker"] for w in watch]
    universe = {**JP_STOCKS, **US_STOCKS}
    names = {**universe, **{w["ticker"]: w["name"] for w in watch}}
    tickers = list(dict.fromkeys(watch_tickers + list(universe)))

    close, vol = get_ohlcv(tickers, "5y")
    items, surges = [], []
    for t in tickers:
        if t not in close:
            continue
        c, v = close[t].dropna(), vol[t].reindex(close[t].dropna().index).fillna(0)
        if len(c) < 30:
            if t in watch_tickers:
                items.append({"name": names[t], "ticker": t, "code": code_of(t),
                              "error": "上場してからの日数が少なく予想できません"})
            continue
        if t in watch_tickers:
            items.append(watch_item(names[t], t, c, v, "ウォッチリスト"))
        else:
            v20 = float(v.iloc[-21:-1].mean())
            if v20 > 0 and float(v.iloc[-1]) / v20 >= 2:
                surges.append((float(v.iloc[-1]) / v20, t, c, v))

    surges.sort(key=lambda x: x[0], reverse=True)
    auto = [watch_item(names[t], t, c, v, "出来高急増") for _, t, c, v in surges[:8]]
    save_json("watch.json", {"updated_at": now_jst(), "watchlist": items, "surge": auto})
    print(f"[注目] ウォッチ {len(items)}銘柄 / 出来高急増 {len(auto)}銘柄")


# ---- 株主優待ランキング -------------------------------------------------

def build_yutai():
    """data/yutai.json（手入力）の各銘柄に、現在の株価から優待利回りを計算する"""
    entries = load_json("yutai.json", [])
    rows = []
    tickers = [f"{e['code']}.T" for e in entries]
    close = get_closes(tickers, "10d") if tickers else pd.DataFrame()
    for e in entries:
        t = f"{e['code']}.T"
        row = dict(e)
        try:
            price = float(close[t].dropna().iloc[-1])
            cost = price * e["shares"]
            row.update({"price": round(price, 1), "cost": round(cost),
                        "yield_pct": round(e["value_yen"] / cost * 100, 2)})
        except Exception:
            row.update({"price": None, "cost": None, "yield_pct": None})
        rows.append(row)
    rows.sort(key=lambda r: r["yield_pct"] if r["yield_pct"] is not None else -1, reverse=True)
    save_json("yutai_ranking.json", {"updated_at": now_jst(), "items": rows})
    print(f"[優待] {len(rows)}銘柄")


# ---- 米国企業の決算解説 -------------------------------------------------

def build_financials():
    import financials
    vr = load_json("value_ranking.json", {}).get("markets", {}).get("us", {}).get("items", [])
    sectors = {r["code"]: r["sector"] for r in vr if r.get("sector") not in (None, "-")}
    items = financials.build(US_STOCKS, sectors)
    ok = sum(1 for i in items if "error" not in i)
    save_json("financials.json", {"updated_at": now_jst(), "count": len(items), "ok": ok, "items": items})
    print(f"[決算] {ok}/{len(items)}社")


if __name__ == "__main__":
    # 1つ失敗しても他は作れるように、個別に実行する
    for step in (build_forecast, build_diagnosis, build_watch, build_ranking, build_value, build_financials, build_yutai):
        try:
            step()
        except Exception as e:
            print(f"[エラー] {step.__name__}: {e}")
