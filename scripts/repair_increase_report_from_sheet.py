import os
import json
import re

import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_sheets_service():
    if not GOOGLE_SHEETS_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
        raise RuntimeError("GOOGLE_SHEETS_ID / GOOGLE_SERVICE_ACCOUNT_JSON 환경변수가 필요합니다.")
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
    return build("sheets", "v4", credentials=credentials)


def read_sheet(service, sheet_name):
    result = service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEETS_ID,
        range=f"{sheet_name}!A:ZZ",
    ).execute()
    values = result.get("values", [])
    if not values:
        return pd.DataFrame()
    header = values[0]
    rows = values[1:]
    width = len(header)
    norm_rows = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in rows]
    return pd.DataFrame(norm_rows, columns=header)


def ensure_sheet_exists(service, sheet_name):
    meta = service.spreadsheets().get(spreadsheetId=GOOGLE_SHEETS_ID).execute()
    names = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if sheet_name in names:
        return

    service.spreadsheets().batchUpdate(
        spreadsheetId=GOOGLE_SHEETS_ID,
        body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
    ).execute()


def write_sheet(service, sheet_name, df):
    ensure_sheet_exists(service, sheet_name)
    values = [list(df.columns)] + df.fillna("").astype(str).values.tolist()

    service.spreadsheets().values().clear(
        spreadsheetId=GOOGLE_SHEETS_ID,
        range=f"{sheet_name}!A:ZZ",
        body={},
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=GOOGLE_SHEETS_ID,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()


def norm_product_no(v):
    if v is None or pd.isna(v):
        return ""
    s = str(v).strip()
    s = re.sub(r"\.0$", "", s)
    return "" if s.lower() in ["nan", "none", "null"] else s


def make_increase_event_key(df):
    """같은 날 동일 작품/요일 post_24h 중 최신 행만 남기기 위한 키."""
    out = pd.Series([""] * len(df), index=df.index, dtype="object")

    if "release_event_key" in df.columns:
        out = df["release_event_key"].fillna("").astype(str).str.strip()

    if "series_product_no" in df.columns:
        pno = df["series_product_no"].apply(norm_product_no)
    else:
        pno = pd.Series([""] * len(df), index=df.index, dtype="object")

    if "webtoon_title_id" in df.columns:
        tid = df["webtoon_title_id"].apply(norm_product_no)
    else:
        tid = pd.Series([""] * len(df), index=df.index, dtype="object")

    if "release_weekday" in df.columns:
        day = df["release_weekday"].fillna("").astype(str).str.strip()
    else:
        day = pd.Series([""] * len(df), index=df.index, dtype="object")

    fallback = pno + "_" + tid + "_" + day
    out = out.where(out.ne(""), fallback)
    return out


REPORT_EXTRA_COLUMNS = [
    "baseline_found",
    "baseline_snapshot_id",
    "baseline_collected_at_kst",
    "baseline_download_text",
    "baseline_download_count",
    "download_increase",
    "download_increase_rate",
]


def report(snap, hist):
    if snap is None or hist is None or snap.empty or hist.empty:
        return pd.DataFrame()

    post = snap[snap["collection_context"] == "post_24h"].copy()
    pre = hist[hist["collection_context"] == "pre_release"].copy()

    if post.empty:
        return pd.DataFrame()

    for c in REPORT_EXTRA_COLUMNS:
        post[c] = None
    post["baseline_found"] = False

    if pre.empty:
        return post

    pre["dt"] = pd.to_datetime(pre["collected_at_kst"], errors="coerce")
    post["dt"] = pd.to_datetime(post["collected_at_kst"], errors="coerce")

    pre["_series_product_no_norm"] = pre["series_product_no"].apply(norm_product_no) if "series_product_no" in pre.columns else ""
    post["_series_product_no_norm"] = post["series_product_no"].apply(norm_product_no) if "series_product_no" in post.columns else ""

    rows = []
    for _, r in post.iterrows():
        out = r.to_dict()
        out.update({
            "baseline_found": False,
            "baseline_snapshot_id": "",
            "baseline_collected_at_kst": "",
            "baseline_download_text": "",
            "baseline_download_count": None,
            "download_increase": None,
            "download_increase_rate": None,
        })

        r_dt = r.get("dt")
        if pd.isna(r_dt):
            rows.append(out)
            continue

        cand = pd.DataFrame()

        if "release_event_key" in pre.columns and "release_event_key" in post.columns:
            key = str(r.get("release_event_key", "")).strip()
            if key:
                cand = pre[
                    (pre["release_event_key"].astype(str).str.strip() == key)
                    & (pre["dt"] < r_dt)
                ]

        if cand.empty:
            pno = r.get("_series_product_no_norm", "")
            day = str(r.get("release_weekday", "")).strip()
            if pno and day and "_series_product_no_norm" in pre.columns and "release_weekday" in pre.columns:
                cand = pre[
                    (pre["_series_product_no_norm"] == pno)
                    & (pre["release_weekday"].astype(str).str.strip() == day)
                    & (pre["dt"] < r_dt)
                ]

        if not cand.empty:
            b = cand.sort_values("dt").iloc[-1]
            cur = pd.to_numeric(r.get("series_download_count"), errors="coerce")
            base = pd.to_numeric(b.get("series_download_count"), errors="coerce")

            if pd.notna(cur) and pd.notna(base):
                inc = float(cur) - float(base)
                rate = inc / float(base) if float(base) != 0 else None
            else:
                inc = None
                rate = None

            out.update({
                "baseline_found": True,
                "baseline_snapshot_id": b.get("snapshot_id", ""),
                "baseline_collected_at_kst": b.get("collected_at_kst", ""),
                "baseline_download_text": b.get("series_download_text", ""),
                "baseline_download_count": base if pd.notna(base) else None,
                "download_increase": inc,
                "download_increase_rate": rate,
            })

        rows.append(out)

    result = pd.DataFrame(rows)
    if "_series_product_no_norm" in result.columns:
        result = result.drop(columns=["_series_product_no_norm"])

    base_cols = [c for c in snap.columns if c in result.columns and c not in REPORT_EXTRA_COLUMNS]
    final_cols = base_cols + [c for c in REPORT_EXTRA_COLUMNS if c in result.columns]
    extra_cols = [c for c in result.columns if c not in final_cols and not c.startswith("_")]
    return result[final_cols + extra_cols]


def main():
    service = get_sheets_service()
    hist = read_sheet(service, "snapshot_history")
    if hist.empty:
        raise RuntimeError("snapshot_history 탭이 비어 있습니다.")

    full = report(hist, hist)

    # 같은 날 Daily를 수동으로 여러 번 실행한 경우,
    # 같은 작품/요일 공개 이벤트는 가장 늦게 수집된 post_24h 1건만 유지합니다.
    if not full.empty:
        full["_dt_for_dedupe"] = pd.to_datetime(full.get("collected_at_kst"), errors="coerce")
        if "collected_date_kst" in full.columns:
            full["_post_collect_date"] = full["collected_date_kst"].fillna("").astype(str).str.strip()
        else:
            full["_post_collect_date"] = full["_dt_for_dedupe"].dt.strftime("%Y-%m-%d")

        full["_increase_event_key"] = make_increase_event_key(full)
        full = (
            full.sort_values(["_post_collect_date", "_increase_event_key", "_dt_for_dedupe"])
            .drop_duplicates(["_post_collect_date", "_increase_event_key"], keep="last")
            .drop(columns=["_dt_for_dedupe", "_post_collect_date", "_increase_event_key"], errors="ignore")
        )

    write_sheet(service, "increase_report", full)
    print(f"Rebuilt increase_report from snapshot_history with latest-per-event dedupe: {len(full)} rows")


if __name__ == "__main__":
    main()
