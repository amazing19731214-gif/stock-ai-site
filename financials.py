"""
米国企業の決算データ（SEC EDGAR）を取得して、日本語の解説を自動で作る

- データ元：米国証券取引委員会（SEC）の公開API（無料）
    https://www.sec.gov/files/company_tickers.json      … ティッカー → CIK番号
    https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json … 決算の数字
- 年次決算（10-K）の過去5年分を使う
- SECに接続できない場合は、Yahoo Finance（yfinance）の年次決算（過去4年程度）を使う
- 解説は数字から決まったルールで作る（AIの文章生成は使っていない）
"""
import json
import time
import urllib.request
from datetime import date

# SECはアクセス元の名前と連絡先をUser-Agentに書くよう求めている
# （書式が合わないと拒否されるため、いくつかの書き方を順に試す）
USER_AGENTS = [
    "StockAiSite amazing19731214-gif@users.noreply.github.com",
    "stock-ai-site/1.0 amazing19731214-gif@users.noreply.github.com",
    "Mozilla/5.0 (compatible; StockAiSite/1.0; +https://github.com/amazing19731214-gif/stock-ai-site) amazing19731214-gif@users.noreply.github.com",
]
_working_ua = None
PROBE_LOG = []

# 指標ごとに、使われる勘定科目の候補（会社や年によって名前が違う）
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "op_cf": ["NetCashProvidedByUsedInOperatingActivities",
              "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "buyback": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}
INSTANT = {"assets", "equity", "cash", "debt"}  # 期末時点の残高（それ以外は1年間の合計）
YEARS = 5


def _get_json(url):
    global _working_ua
    import urllib.error
    candidates = [_working_ua] if _working_ua else USER_AGENTS
    last = None
    for ua in candidates:
        req = urllib.request.Request(url, headers={
            "User-Agent": ua, "Accept-Encoding": "identity", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
            _working_ua = ua
            return data
        except urllib.error.HTTPError as e:
            PROBE_LOG.append(f"{url.split('/')[2]} {e.code} UA={ua[:40]}")
            last = e
            time.sleep(1)
    raise last


def load_cik_map():
    data = _get_json("https://www.sec.gov/files/company_tickers.json")
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def _days(start, end):
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def annual_series(facts, key):
    """{決算期末日: 値} を返す。候補の勘定科目を優先順に組み合わせる"""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    result = {}
    for concept in CONCEPTS[key]:
        units = gaap.get(concept, {}).get("units", {})
        rows = units.get("USD") or units.get("USD/shares") or []
        best = {}
        for r in rows:
            if r.get("form") not in ("10-K", "10-K/A"):
                continue
            if key not in INSTANT:
                if "start" not in r or not 340 <= _days(r["start"], r["end"]) <= 390:
                    continue
            # 同じ期末の数字が何度も出てくる（翌年の決算書にも載る）ので、最新の提出を使う
            if r["end"] not in best or r["filed"] > best[r["end"]][0]:
                best[r["end"]] = (r["filed"], r["val"])
        for end, (_, val) in best.items():
            result.setdefault(end, val)
    return result


def fiscal_year_ends(facts):
    """年次決算の期末日（新しい順）"""
    ends = set()
    for key in ("revenue", "net_income", "op_cf"):
        ends |= set(annual_series(facts, key))
    # 期末日が数日ずれる会社（52/53週決算）があるので、近い日付はまとめる
    out = []
    for e in sorted(ends, reverse=True):
        if not out or _days(e, out[-1]) > 300:  # 前の期末と300日以上離れていれば別の年度
            out.append(e)
    return out[:YEARS]


def pick(series, end):
    """期末日がぴったり一致しなくても、前後10日以内なら同じ期として使う"""
    if end in series:
        return series[end]
    for k, v in series.items():
        if abs(_days(k, end)) <= 10:
            return v
    return None


def build_table(facts):
    ends = fiscal_year_ends(facts)
    if len(ends) < 2:
        return None
    series = {k: annual_series(facts, k) for k in CONCEPTS}
    years = []
    for end in sorted(ends):
        row = {"end": end, "fy": f"{end[:4]}年{int(end[5:7])}月期"}
        for k in CONCEPTS:
            v = pick(series[k], end)
            row[k] = round(v, 2) if k == "eps" and v is not None else v
        if row["op_cf"] is not None:
            row["fcf"] = row["op_cf"] - (row["capex"] or 0)
        else:
            row["fcf"] = None
        years.append(row)
    return years


# ---- 解説文を作る ---------------------------------------------------------

def _pct(a, b):
    return (a / b - 1) * 100 if a is not None and b not in (None, 0) and b > 0 else None


def _ratio(a, b):
    return a / b * 100 if a is not None and b not in (None, 0) else None


def usd(v):
    """ドルを日本語の単位で表す（例：3,915億ドル）"""
    if v is None:
        return "-"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e12:
        return f"{sign}{a / 1e12:.2f}兆ドル"
    if a >= 1e8:
        return f"{sign}{a / 1e8:,.0f}億ドル"
    if a >= 1e4:
        return f"{sign}{a / 1e4:,.0f}万ドル"
    return f"{sign}{a:,.0f}ドル"


def analyze(years, sector=""):
    last, prev, first = years[-1], years[-2], years[0]
    n = len(years) - 1
    is_fin = sector == "金融"
    points = {}
    notes = {"growth": [], "profit": [], "cash": [], "safety": [], "return": []}

    # 成長性
    g = _pct(last["revenue"], prev["revenue"])
    cagr = None
    if last["revenue"] and first["revenue"] and first["revenue"] > 0 and n > 0:
        cagr = ((last["revenue"] / first["revenue"]) ** (1 / n) - 1) * 100
    streak = 0
    for a, b in zip(reversed(years[1:]), reversed(years[:-1])):
        if a["revenue"] and b["revenue"] and a["revenue"] > b["revenue"]:
            streak += 1
        else:
            break
    if g is not None:
        word = ("大きく伸びました" if g >= 20 else "伸びました" if g >= 5 else
                "ほぼ横ばいでした" if g > -5 else "減りました")
        notes["growth"].append(f"直近の売上高は{usd(last['revenue'])}で、前年から{g:+.1f}%と{word}。")
    if cagr is not None and n >= 2:
        notes["growth"].append(f"過去{n}年の売上は年平均{cagr:+.1f}%のペースで変化しています。")
    if streak >= 3:
        notes["growth"].append(f"{streak}年連続の増収です。")
    base = cagr if cagr is not None else g
    points["growth"] = None if base is None else (
        25 if base >= 15 else 20 if base >= 8 else 14 if base >= 3 else 8 if base >= 0 else 3)

    # 収益性
    opm = _ratio(last["operating_income"], last["revenue"])
    opm_prev = _ratio(prev["operating_income"], prev["revenue"])
    npm = _ratio(last["net_income"], last["revenue"])
    if opm is not None:
        level = ("非常に高い" if opm >= 25 else "高い" if opm >= 15 else
                 "標準的な" if opm >= 5 else "低い" if opm >= 0 else "赤字の")
        notes["profit"].append(f"本業のもうけを示す営業利益率は{opm:.1f}%で、{level}水準です。")
        if opm_prev is not None and abs(opm - opm_prev) >= 2:
            notes["profit"].append(f"前年（{opm_prev:.1f}%）から{'改善' if opm > opm_prev else '悪化'}しました。")
    if last["net_income"] is not None:
        ni_g = _pct(last["net_income"], prev["net_income"])
        if last["net_income"] < 0:
            notes["profit"].append(f"最終損益は{usd(last['net_income'])}の赤字です。")
        elif ni_g is not None:
            notes["profit"].append(f"純利益は{usd(last['net_income'])}で、前年比{ni_g:+.1f}%です。")
        else:
            notes["profit"].append(f"純利益は{usd(last['net_income'])}で、黒字に転換しました。"
                                   if prev["net_income"] is not None and prev["net_income"] < 0
                                   else f"純利益は{usd(last['net_income'])}です。")
    m = opm if opm is not None else npm
    points["profit"] = None if m is None else (
        25 if m >= 25 else 20 if m >= 15 else 13 if m >= 8 else 8 if m >= 3 else 4 if m >= 0 else 0)

    # 現金を稼ぐ力
    fcf = last["fcf"]
    fcf_m = _ratio(fcf, last["revenue"])
    if fcf is not None:
        if fcf >= 0:
            notes["cash"].append(f"設備投資を差し引いた後に手元に残る現金（フリーキャッシュフロー）は{usd(fcf)}"
                                 + (f"で、売上の{fcf_m:.1f}%です。" if fcf_m is not None else "です。"))
        else:
            notes["cash"].append(f"フリーキャッシュフローは{usd(fcf)}のマイナスで、"
                                 "本業の現金収入より投資などの支出が多い状態です。")
        neg_years = sum(1 for y in years if y["fcf"] is not None and y["fcf"] < 0)
        if neg_years == 0 and len(years) >= 3:
            notes["cash"].append(f"過去{len(years)}年、毎年プラスを保っています。")
    if is_fin:
        notes["cash"].append("金融機関は事業の性質上、キャッシュフローの数字が大きくぶれやすいため参考程度に見てください。")
        points["cash"] = None
    else:
        points["cash"] = None if fcf_m is None else (
            25 if fcf_m >= 20 else 20 if fcf_m >= 10 else 14 if fcf_m >= 5 else 8 if fcf_m >= 0 else 2)

    # 財務の安全性
    eq_ratio = _ratio(last["equity"], last["assets"])
    if last["equity"] is not None and last["equity"] < 0:
        notes["safety"].append("株主資本がマイナスです。大規模な自社株買いを続けている会社や、"
                               "過去の損失が積み上がった会社で見られる状態です。")
        points["safety"] = 8
    elif eq_ratio is not None:
        if is_fin:
            notes["safety"].append(f"自己資本比率は{eq_ratio:.1f}%です。銀行などの金融機関は、預金を元手に"
                                   "事業をするため、この比率がもともと低くなります。")
            points["safety"] = None
        else:
            level = "厚く、余裕があります" if eq_ratio >= 50 else "標準的です" if eq_ratio >= 30 else "低めです"
            notes["safety"].append(f"総資産のうち返済不要の資本が占める割合（自己資本比率）は{eq_ratio:.1f}%で、{level}。")
            points["safety"] = 25 if eq_ratio >= 50 else 18 if eq_ratio >= 30 else 10 if eq_ratio >= 15 else 6
    else:
        points["safety"] = None
    if last["cash"] is not None and last["debt"] is not None and not is_fin:
        if last["cash"] >= last["debt"]:
            notes["safety"].append(f"現金（{usd(last['cash'])}）が長期借入金（{usd(last['debt'])}）を上回っています。")
        else:
            notes["safety"].append(f"現金は{usd(last['cash'])}、長期借入金は{usd(last['debt'])}です。")

    # 株主還元
    payout = (last["buyback"] or 0) + (last["dividends"] or 0)
    if payout > 0:
        parts = []
        if last["dividends"]:
            parts.append(f"配当{usd(last['dividends'])}")
        if last["buyback"]:
            parts.append(f"自社株買い{usd(last['buyback'])}")
        s = "株主への還元は" + "と".join(parts) + f"で、合計{usd(payout)}です。"
        if fcf and fcf > 0:
            r = payout / fcf * 100
            s += f"フリーキャッシュフローの{r:.0f}%にあたり、" + (
                "稼いだ現金以上を還元しています。" if r > 100 else "稼いだ現金の範囲内です。")
        notes["return"].append(s)
    else:
        notes["return"].append("直近の年は、配当や自社株買いによる株主還元はありませんでした。")

    # 財務スコア（取れた項目だけで100点満点に換算。3項目未満なら出さない）
    got = [v for v in points.values() if v is not None]
    score = round(sum(got) / (25 * len(got)) * 100) if len(got) >= 3 else None

    # ひとことでいうと
    tags = []
    if base is not None and base >= 15:
        tags.append("高成長")
    elif base is not None and base < 0:
        tags.append("減収傾向")
    if m is not None and m >= 20:
        tags.append("高収益")
    elif m is not None and m < 0:
        tags.append("赤字")
    if fcf is not None and fcf < 0 and not is_fin:
        tags.append("現金流出")
    if payout and fcf and fcf > 0 and payout / fcf >= 0.8:
        tags.append("還元に積極的")
    if not tags:
        tags.append("安定型")

    summary = [x[0] for x in (notes["growth"], notes["profit"], notes["safety"]) if x]
    return {
        "score": score, "points": points, "tags": tags, "summary": summary, "notes": notes,
        "metrics": {
            "revenue_growth": round(g, 1) if g is not None else None,
            "revenue_cagr": round(cagr, 1) if cagr is not None else None,
            "op_margin": round(opm, 1) if opm is not None else None,
            "net_margin": round(npm, 1) if npm is not None else None,
            "fcf_margin": round(fcf_m, 1) if fcf_m is not None else None,
            "equity_ratio": round(eq_ratio, 1) if eq_ratio is not None else None,
        },
    }


# ---- 予備のデータ元：Yahoo Finance（yfinance） ---------------------------

YF_ROWS = {
    "revenue": ("income_stmt", ["Total Revenue", "Operating Revenue"]),
    "operating_income": ("income_stmt", ["Operating Income"]),
    "net_income": ("income_stmt", ["Net Income", "Net Income Common Stockholders"]),
    "eps": ("income_stmt", ["Diluted EPS", "Basic EPS"]),
    "op_cf": ("cashflow", ["Operating Cash Flow"]),
    "capex": ("cashflow", ["Capital Expenditure"]),
    "buyback": ("cashflow", ["Repurchase Of Capital Stock", "Common Stock Payments"]),
    "dividends": ("cashflow", ["Cash Dividends Paid", "Common Stock Dividend Paid"]),
    "assets": ("balance_sheet", ["Total Assets"]),
    "equity": ("balance_sheet", ["Stockholders Equity", "Common Stock Equity"]),
    "cash": ("balance_sheet", ["Cash And Cash Equivalents"]),
    "debt": ("balance_sheet", ["Long Term Debt", "Total Debt"]),
}
POSITIVE = {"capex", "buyback", "dividends"}  # Yahooでは支出がマイナスで入っているので絶対値にする


def build_table_yahoo(ticker):
    import math
    import yfinance as yf
    t = yf.Ticker(ticker)
    sheets = {"income_stmt": t.income_stmt, "cashflow": t.cashflow, "balance_sheet": t.balance_sheet}
    inc = sheets["income_stmt"]
    if inc is None or inc.empty:
        return None, ""
    cols = sorted(inc.columns)[-YEARS:]
    years = []
    for col in cols:
        end = col.strftime("%Y-%m-%d")
        row = {"end": end, "fy": f"{end[:4]}年{int(end[5:7])}月期"}
        for key, (sheet, names) in YF_ROWS.items():
            df, val = sheets[sheet], None
            if df is not None and not df.empty:
                # 貸借対照表などは期末日が数日ずれることがあるので、近い列を探す
                near = [c for c in df.columns if abs((c - col).days) <= 10]
                for name in names:
                    if near and name in df.index:
                        v = df.loc[name, near[0]]
                        if v is not None and not (isinstance(v, float) and math.isnan(v)):
                            val = float(v)
                            break
            if val is not None and key in POSITIVE:
                val = abs(val)
            row[key] = round(val, 2) if key == "eps" and val is not None else val
        row["fcf"] = row["op_cf"] - (row["capex"] or 0) if row["op_cf"] is not None else None
        years.append(row)
    # 売上がない年（データ欠け）は外す
    years = [y for y in years if y["revenue"] is not None or y["net_income"] is not None]
    try:
        en = t.info.get("longName", "") if hasattr(t, "info") else ""
    except Exception:
        en = ""
    return (years if len(years) >= 2 else None), en


def probe_sec_data():
    """data.sec.gov（決算データの窓口）に直接つながるか確認する（アップル：CIK 320193）"""
    try:
        _get_json("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
        return True
    except Exception as e:
        PROBE_LOG.append(f"data.sec.gov probe: {e}")
        return False


def build(stocks: dict, sectors: dict, log=print):
    """stocks: {ティッカー: 日本語名}, sectors: {ティッカー: 業種}
    戻り値: (各社のデータ, データ元の名前)"""
    try:
        cik_map = load_cik_map()
        source = "SEC"
    except Exception as e:
        log(f"[決算] SECに接続できないため、Yahoo Financeのデータを使います（{e}）")
        probe_sec_data()
        cik_map, source = None, "Yahoo Finance"

    out = []
    for ticker, name in stocks.items():
        sector = sectors.get(ticker, "")
        try:
            if source == "SEC":
                cik = cik_map.get(ticker.upper()) or cik_map.get(ticker.upper().replace("-", "."))
                if not cik:
                    raise ValueError("CIK番号が見つかりません")
                facts = _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
                years, en = build_table(facts), facts.get("entityName", "")
                time.sleep(0.2)  # SECのアクセス制限（毎秒10回まで）より十分ゆっくり
            else:
                years, en = build_table_yahoo(ticker)
            if not years:
                raise ValueError("年次決算が足りません")
            item = {"ticker": ticker, "name": name, "en_name": en, "sector": sector, "years": years}
            item.update(analyze(years, sector))
            out.append(item)
            log(f"[決算] OK {name}: {years[-1]['fy']} スコア{item['score']}")
        except Exception as e:
            log(f"[決算] NG {name}: {e}")
            out.append({"ticker": ticker, "name": name, "error": "決算データを読み込めませんでした"})
    return out, source
